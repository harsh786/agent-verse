"""Data classification schema."""
from __future__ import annotations
import enum
from dataclasses import dataclass, field


class DataClass(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"
    PII = "pii"
    PHI = "phi"
    PCI = "pci"
    SOURCE_CODE = "source_code"

    @classmethod
    def sensitivity_order(cls) -> list["DataClass"]:
        return [cls.PUBLIC, cls.INTERNAL, cls.CONFIDENTIAL, cls.SOURCE_CODE,
                cls.PII, cls.PHI, cls.PCI, cls.SECRET]


@dataclass
class DataClassification:
    data_id: str
    classes: list[DataClass]
    detected_entities: list[str] = field(default_factory=list)
    retention_policy: str = "default"
    allowed_sinks: list[str] = field(default_factory=lambda: ["tenant_user", "audit_log"])
    blocked_sinks: list[str] = field(default_factory=list)

    @property
    def highest_sensitivity(self) -> DataClass:
        order = DataClass.sensitivity_order()
        best = DataClass.PUBLIC
        for cls in self.classes:
            if order.index(cls) > order.index(best):
                best = cls
        return best

    @property
    def safe_for_prompt(self) -> bool:
        sensitive = {DataClass.SECRET, DataClass.PHI, DataClass.PCI}
        return not bool(set(self.classes) & sensitive)
