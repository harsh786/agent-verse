"""TRG-25: CloudWatch triggers receive alarms through a confirmed SNS subscription.

CloudWatch reaches HTTPS only via SNS; nothing handled SNS's
SubscriptionConfirmation (so the subscription never confirmed) or unwrapped a
Notification's ``Message``, so no alarm was ever delivered.
"""

from __future__ import annotations

import base64
import datetime
import json
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.webhooks import sns
from tests.triggers.test_typed_webhook_tenant_boundary import TOK, _app

CERT_URL = "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-abc.pem"
SUB_URL = (
    "https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription"
    "&TopicArn=arn:aws:sns:us-east-1:123456789012:alarms&Token=tok"
)
TOPIC = "arn:aws:sns:us-east-1:123456789012:alarms"


def _keypair() -> tuple[Any, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM)


KEY, CERT_PEM = _keypair()


def _signed(msg: dict[str, Any], *, version: str = "1") -> dict[str, Any]:
    msg = {**msg, "SignatureVersion": version, "SigningCertURL": CERT_URL}
    algo = hashes.SHA1() if version == "1" else hashes.SHA256()
    sig = KEY.sign(sns.string_to_sign(msg), padding.PKCS1v15(), algo)
    msg["Signature"] = base64.b64encode(sig).decode()
    return msg


def _confirmation() -> dict[str, Any]:
    return _signed(
        {
            "Type": "SubscriptionConfirmation",
            "MessageId": "165545c9-2a5c-472c-8df2-7ff2be2b3b1b",
            "Token": "tok",
            "TopicArn": TOPIC,
            "Message": "You have chosen to subscribe to the topic ...",
            "SubscribeURL": SUB_URL,
            "Timestamp": "2026-09-30T12:00:00.000Z",
        }
    )


def _notification(alarm: dict[str, Any], *, message_id: str = "n-1") -> dict[str, Any]:
    return _signed(
        {
            "Type": "Notification",
            "MessageId": message_id,
            "TopicArn": TOPIC,
            "Subject": 'ALARM: "HighErrors" in US East (N. Virginia)',
            "Message": json.dumps(alarm),
            "Timestamp": "2026-09-30T12:01:00.000Z",
        },
        version="2",
    )


class _Store:
    def __init__(self) -> None:
        self.spec = TriggerSpec(trigger_type=TriggerType.CLOUDWATCH, webhook_token=TOK)
        self.updated: list[TriggerSpec] = []

    async def find_tenant_by_webhook_token(self, token: str, **_k: Any) -> Any:
        return "t1" if token == TOK else None

    async def find_by_type_async(self, trigger_type: str, *, tenant_id: str, **_k: Any) -> list[Any]:
        assert trigger_type == "cloudwatch"
        return [{"schedule_id": "s-cw", "spec": self.spec, "goal_template": "Investigate"}]

    async def update_async(self, schedule_id: str, *, tenant_ctx: Any, spec: TriggerSpec) -> Any:
        self.updated.append(spec)
        return {}


class _Dispatcher:
    def __init__(self) -> None:
        self.fired: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def dispatch(self, spec: Any, payload: dict[str, Any], tenant_ctx: Any, **kw: Any) -> Any:
        self.fired.append((payload, kw))
        return SimpleNamespace(goal_created=True)


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake_fetch(url: str) -> Any:
        calls.append(url)
        return SimpleNamespace(content=CERT_PEM if url == CERT_URL else b"<ok/>")

    sns._CERT_CACHE.clear()
    monkeypatch.setattr(sns, "_fetch_https", fake_fetch)
    return calls


def _post(client: Any, msg: dict[str, Any], sns_type: str) -> Any:
    return client.post(
        f"/triggers/webhooks/cloudwatch/{TOK}",
        content=json.dumps(msg),
        headers={"Content-Type": "text/plain; charset=UTF-8", "x-amz-sns-message-type": sns_type},
    )


def test_subscription_confirmation_is_confirmed_once(fetched: list[str]) -> None:
    store, disp = _Store(), _Dispatcher()
    client = _app(store, disp, caller=None)  # type: ignore[arg-type]

    r = _post(client, _confirmation(), "SubscriptionConfirmation")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "subscription_confirmed"
    assert fetched.count(SUB_URL) == 1
    assert disp.fired == []
    assert store.updated and store.updated[0].sns_subscription_confirmed_at


def test_alarm_notification_dispatches_the_alarm(fetched: list[str]) -> None:
    store, disp = _Store(), _Dispatcher()
    client = _app(store, disp, caller=None)  # type: ignore[arg-type]
    alarm = {"AlarmName": "HighErrors", "NewStateValue": "ALARM", "NewStateReason": "Threshold"}

    r = _post(client, _notification(alarm), "Notification")

    assert r.status_code == 200, r.text
    [(payload, kw)] = disp.fired
    assert payload["AlarmName"] == "HighErrors"
    assert payload["NewStateValue"] == "ALARM"
    assert payload["sns_topic_arn"] == TOPIC
    assert kw == {"message_id": "n-1"}  # SNS redeliveries dedup on MessageId
    assert SUB_URL not in fetched


def test_forged_or_tampered_sns_message_is_rejected(fetched: list[str]) -> None:
    store, disp = _Store(), _Dispatcher()
    client = _app(store, disp, caller=None)  # type: ignore[arg-type]
    tampered = _notification({"AlarmName": "HighErrors"})
    tampered["Message"] = json.dumps({"AlarmName": "Injected"})
    evil_cert = {**_confirmation(), "SigningCertURL": "https://evil.example.com/cert.pem"}
    evil_sub = _confirmation()
    evil_sub["SubscribeURL"] = "https://169.254.169.254/latest/meta-data"
    evil_sub = _signed({k: v for k, v in evil_sub.items() if k != "Signature"})

    assert _post(client, tampered, "Notification").status_code == 401
    assert _post(client, evil_cert, "SubscriptionConfirmation").status_code == 401
    assert _post(client, evil_sub, "SubscriptionConfirmation").status_code == 400
    assert disp.fired == []
    assert all(u == CERT_URL for u in fetched)  # nothing but the SNS cert was fetched


def test_sns_url_allowlist() -> None:
    assert sns.is_sns_url(CERT_URL, pem=True)
    assert sns.is_sns_url("https://sns.cn-north-1.amazonaws.com.cn/x.pem", pem=True)
    assert not sns.is_sns_url("http://sns.us-east-1.amazonaws.com/x.pem", pem=True)
    assert not sns.is_sns_url("https://sns.us-east-1.amazonaws.com.evil.io/x.pem", pem=True)
    assert not sns.is_sns_url("https://s3.amazonaws.com/x.pem", pem=True)
    assert not sns.is_sns_url("https://sns.us-east-1.amazonaws.com/x.txt", pem=True)
