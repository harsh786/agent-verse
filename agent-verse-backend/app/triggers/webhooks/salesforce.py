"""Salesforce Outbound Message (SOAP) handling for the typed webhook (TRG-26).

Salesforce workflow/flow outbound messages are SOAP XML, not JSON: the typed
webhook parsed JSON only, so every message fired with an empty payload, and
Salesforce — which redelivers until it receives an ``<Ack>true</Ack>`` — kept
retrying it for 24 hours.

Parsed with the stdlib ElementTree after refusing any DOCTYPE/ENTITY
declaration (no external entities or entity expansion), the same guard as the
RSS parser; the body is untrusted external input.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

_UNSAFE_XML = re.compile(rb"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

ACK_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soapenv:Body>"
    '<notificationsResponse xmlns="http://soap.sforce.com/2005/09/outbound">'
    "<Ack>true</Ack></notificationsResponse>"
    "</soapenv:Body></soapenv:Envelope>"
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element) -> str | None:
    if el.get(_XSI_NIL) == "true":
        return None
    return (el.text or "").strip()


def looks_like_xml(body: bytes) -> bool:
    return body.lstrip().startswith(b"<")


def parse_outbound_message(body: bytes) -> dict[str, Any]:
    """Parse a Salesforce outbound message into a payload dict.

    Raises ``ValueError`` for unsafe or malformed XML, or XML that is not an
    outbound message (no ``notifications`` element).
    """
    if _UNSAFE_XML.search(body):
        raise ValueError("DOCTYPE/ENTITY declarations are not accepted")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"malformed XML: {exc}") from exc
    notifications_el = next((el for el in root.iter() if _local(el.tag) == "notifications"), None)
    if notifications_el is None:
        raise ValueError("not a Salesforce outbound message")

    payload: dict[str, Any] = {"notifications": []}
    for child in notifications_el:
        name = _local(child.tag)
        if name == "Notification":
            note: dict[str, Any] = {"id": "", "sobject_type": "", "fields": {}}
            for part in child:
                part_name = _local(part.tag)
                if part_name == "Id":
                    note["id"] = _text(part) or ""
                elif part_name == "sObject":
                    note["sobject_type"] = (part.get(_XSI_TYPE) or "").split(":")[-1]
                    note["fields"] = {_local(f.tag): _text(f) for f in part}
            payload["notifications"].append(note)
        else:
            # OrganizationId, ActionId, SessionId, EnterpriseUrl, PartnerUrl
            key = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
            payload[key] = _text(child)
    if payload["notifications"]:
        first = payload["notifications"][0]
        payload["sobject_type"] = first["sobject_type"]
        payload["sobject"] = first["fields"]
    return payload


def message_id(payload: dict[str, Any]) -> str:
    """Stable idempotency input: Salesforce redelivers the same notification ids."""
    return ",".join(sorted(str(n.get("id") or "") for n in payload.get("notifications", [])))
