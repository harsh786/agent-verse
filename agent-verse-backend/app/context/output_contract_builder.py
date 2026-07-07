"""OutputContractBuilder — builds output format contract for executor responses."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class OutputSchema:
    output_format: str
    required_fields: list[str] = field(default_factory=list)
    schema: dict = field(default_factory=dict)
    instructions: str = ""


class OutputContractBuilder:
    def build(
        self,
        output_format: str = "text",
        required_fields: list[str] | None = None,
    ) -> OutputSchema:
        fields = required_fields or []
        instructions = ""
        if output_format == "json":
            instructions = f"Return ONLY valid JSON with fields: {fields}"
        elif output_format == "markdown":
            instructions = "Return formatted Markdown."
        return OutputSchema(
            output_format=output_format,
            required_fields=fields,
            instructions=instructions,
        )
