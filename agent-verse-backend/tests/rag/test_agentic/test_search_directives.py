"""[SEARCH:type:"query"] directives in step descriptions must be parsed and executed."""
from __future__ import annotations

import pytest

from app.rag.agentic.search_directive_parser import SearchDirectiveParser


@pytest.fixture
def parser():
    return SearchDirectiveParser()


def test_parse_kb_directive(parser):
    step = 'Research background: [SEARCH:kb:"dynamic orchestration patterns"] then summarize'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "kb"
    assert "orchestration" in directives[0].query


def test_parse_web_directive(parser):
    step = 'Get latest: [SEARCH:web:"Python 3.12 release notes"]'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "web"
    assert "Python 3.12" in directives[0].query


def test_parse_graph_directive(parser):
    step = 'Find related: [SEARCH:graph:"AgentVerse dependencies"]'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "graph"


def test_parse_memory_directive(parser):
    step = 'Recall: [SEARCH:memory:"past Jira automation goals"]'
    directives = parser.extract(step)
    assert len(directives) == 1
    assert directives[0].source_type == "memory"


def test_parse_multiple_directives(parser):
    step = '[SEARCH:kb:"internal docs"] and also [SEARCH:web:"latest version"]'
    directives = parser.extract(step)
    assert len(directives) == 2
    sources = {d.source_type for d in directives}
    assert "kb" in sources
    assert "web" in sources


def test_no_directive_returns_empty(parser):
    step = "Just execute the step without any search."
    assert parser.extract(step) == []


def test_directive_map_to_retrieval_strategy(parser):
    assert parser.directive_to_strategy("kb") == "hybrid"
    assert parser.directive_to_strategy("web") == "web"
    assert parser.directive_to_strategy("graph") == "graph"
    assert parser.directive_to_strategy("memory") == "memory"
    assert parser.directive_to_strategy("unknown") == "auto"


def test_strip_directives_from_step(parser):
    step = 'Step: [SEARCH:kb:"query"] then do the analysis'
    cleaned = parser.strip_directives(step)
    assert "[SEARCH:" not in cleaned
    assert "then do the analysis" in cleaned


def test_directive_with_single_quotes(parser):
    step = "[SEARCH:kb:'single quote query']"
    directives = parser.extract(step)
    assert len(directives) == 1
    assert "single quote query" in directives[0].query
