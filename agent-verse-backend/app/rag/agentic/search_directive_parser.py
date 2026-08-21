"""SearchDirectiveParser — parses [SEARCH:type:"query"] directives from plan steps."""

from __future__ import annotations

import re
from dataclasses import dataclass

_DIRECTIVE_PATTERN = re.compile(
    r'\[SEARCH:(\w+):["\']([^"\']+)["\']\]',
    re.IGNORECASE,
)

_STRATEGY_MAP: dict[str, str] = {
    "kb": "hybrid",
    "web": "web",
    "graph": "graph",
    "memory": "memory",
}


@dataclass
class SearchDirective:
    source_type: str
    query: str
    raw: str


class SearchDirectiveParser:
    def extract(self, step_text: str) -> list[SearchDirective]:
        directives = []
        for match in _DIRECTIVE_PATTERN.finditer(step_text):
            directives.append(
                SearchDirective(
                    source_type=match.group(1).lower(),
                    query=match.group(2).strip(),
                    raw=match.group(0),
                )
            )
        return directives

    def strip_directives(self, step_text: str) -> str:
        return _DIRECTIVE_PATTERN.sub("", step_text).strip()

    def directive_to_strategy(self, source_type: str) -> str:
        return _STRATEGY_MAP.get(source_type.lower(), "auto")
