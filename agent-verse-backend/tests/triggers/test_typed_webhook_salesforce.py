"""TRG-26: Salesforce outbound messages (SOAP XML) fire with their fields and get an Ack.

The typed webhook parsed JSON only: the XML became ``{}`` and Salesforce, which
redelivers until it receives ``<Ack>true</Ack>``, never got one.
"""

from __future__ import annotations

from typing import Any

from app.triggers.webhooks.salesforce import parse_outbound_message
from tests.triggers.test_typed_webhook_tenant_boundary import TOK, _app, _spec, _Store

OUTBOUND = b"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <soapenv:Body>
  <notifications xmlns="http://soap.sforce.com/2005/09/outbound">
   <OrganizationId>00D000000000001</OrganizationId>
   <ActionId>04k000000000001</ActionId>
   <SessionId xsi:nil="true"/>
   <EnterpriseUrl>https://acme.my.salesforce.com/services/Soap/c/59.0</EnterpriseUrl>
   <PartnerUrl>https://acme.my.salesforce.com/services/Soap/u/59.0</PartnerUrl>
   <Notification>
    <Id>04l000000000001</Id>
    <sObject xsi:type="sf:Opportunity" xmlns:sf="urn:sobject.enterprise.soap.sforce.com">
     <sf:Id>006000000000001</sf:Id>
     <sf:Name>Big deal</sf:Name>
     <sf:StageName>Closed Won</sf:StageName>
    </sObject>
   </Notification>
  </notifications>
 </soapenv:Body>
</soapenv:Envelope>"""


class _Dispatcher:
    def __init__(self) -> None:
        self.fired: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def dispatch(self, spec: Any, payload: dict[str, Any], ctx: Any, **kw: Any) -> None:
        self.fired.append((payload, kw))


def test_outbound_message_fires_with_its_fields_and_is_acked() -> None:
    disp = _Dispatcher()
    client = _app(_Store({"t1": [_spec(TOK)]}), disp, caller=None)  # type: ignore[arg-type]

    r = client.post(
        f"/triggers/webhooks/salesforce/{TOK}",
        content=OUTBOUND,
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
    )

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/xml")
    assert "<Ack>true</Ack>" in r.text
    [(payload, kw)] = disp.fired
    assert payload["sobject_type"] == "Opportunity"
    assert payload["sobject"]["Name"] == "Big deal"
    assert payload["organization_id"] == "00D000000000001"
    assert kw == {"message_id": "04l000000000001"}


def test_hostile_or_malformed_xml_is_rejected_without_ack() -> None:
    disp = _Dispatcher()
    client = _app(_Store({"t1": [_spec(TOK)]}), disp, caller=None)  # type: ignore[arg-type]
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'

    for body in (bomb, b"<notifications><broken>"):
        r = client.post(
            f"/triggers/webhooks/salesforce/{TOK}",
            content=body,
            headers={"Content-Type": "text/xml"},
        )
        assert r.status_code == 400, r.text
        assert "Ack" not in r.text
    assert disp.fired == []


def test_parser_unit() -> None:
    payload = parse_outbound_message(OUTBOUND)
    assert payload["session_id"] is None
    assert payload["notifications"][0]["id"] == "04l000000000001"
