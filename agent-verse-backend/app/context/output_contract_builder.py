"""OutputContractBuilder — builds output format contract for executor responses."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class OutputSchema:
    output_format: str
    required_fields: list[str] = field(default_factory=list)
    schema: dict = field(default_factory=dict)
    instructions: str = ""


_JSON_SIGNALS = frozenset({
    "json", "table", "csv", "structured", "data", "dict", "array",
    "output as", "return as", "format as", "fields:", "columns:",
})
_MARKDOWN_SIGNALS = frozenset({
    "report", "document", "readme", "markdown", "formatted", "write a",
    "create a document", "draft", "article", "essay", "summary",
})


class OutputContractBuilder:
    """Builds output format contracts with goal-aware auto-detection."""

    def build(
        self,
        output_format: str = "auto",
        required_fields: list[str] | None = None,
        goal: str = "",
    ) -> OutputSchema:
        """Build an OutputSchema. When output_format='auto', detects from goal text."""
        fields = required_fields or []

        # Auto-detect format from goal text
        if output_format in ("auto", "") and goal:
            g_lower = goal.lower()
            if any(s in g_lower for s in _JSON_SIGNALS):
                output_format = "json"
            elif any(s in g_lower for s in _MARKDOWN_SIGNALS):
                output_format = "markdown"
            else:
                output_format = "text"
        elif output_format in ("auto", ""):
            output_format = "text"

        # Build instructions
        instructions = ""
        if output_format == "json":
            if fields:
                instructions = (
                    f"Return ONLY valid JSON with these exact fields: {fields}. "
                    "No markdown code fences, no explanation text."
                )
            else:
                instructions = (
                    "Return ONLY valid JSON. No markdown code fences, no explanation text."
                )
        elif output_format == "markdown":
            instructions = (
                "Structure your response using proper Markdown: "
                "## headings, **bold** for key terms, - bullet points for lists, "
                "and ```code``` blocks for code snippets."
            )
        elif output_format == "text":
            instructions = (
                "Provide a clear, concise response. Be specific and actionable."
            )

        return OutputSchema(
            output_format=output_format,
            required_fields=fields,
            instructions=instructions,
        )
