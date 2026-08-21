from __future__ import annotations


class SourceExplainer:
    def explain_fallback(
        self,
        original_source: str,
        fallback_source: str,
        reason: str,
    ) -> str:
        return (
            f"Retrieval from '{original_source}' fell back to '{fallback_source}'. Reason: {reason}"
        )

    def explain_source_selection(self, sources: list[str], reason: str) -> str:
        return f"Selected sources {sources} because: {reason}"
