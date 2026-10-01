"""Build the real-world JSON + markdown report.

    python -m tests.real_world.report <backend-results.jsonl> <playwright.json> <out-dir>

Writes ``<out-dir>/real_world_report.json`` and ``<out-dir>/real_world_report.md``:
one row per scenario test (scenario, result, duration, key evidence, failure detail).
All text is masked (the tenant key never reaches a report).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from tests.real_world.helpers import load_api_key, mask


def _backend_rows(path: str) -> list[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _walk_specs(suite: dict[str, Any]) -> list[dict[str, Any]]:
    out = list(suite.get("specs", []))
    for child in suite.get("suites", []):
        out.extend(_walk_specs(child))
    return out


def _playwright_rows(path: str) -> list[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows: list[dict[str, Any]] = []
    for suite in data.get("suites", []):
        for spec in _walk_specs(suite):
            for t in spec.get("tests", []):
                results = t.get("results") or [{}]
                last = results[-1]
                status = last.get("status") or t.get("status") or "unknown"
                result = {"passed": "passed", "failed": "failed", "timedOut": "failed",
                          "skipped": "skipped", "interrupted": "failed"}.get(status, status)
                errors = [e.get("message", "") for e in last.get("errors", [])] or (
                    [last["error"].get("message", "")] if last.get("error") else [])
                title = spec.get("title", "")
                rows.append({
                    "suite": "playwright",
                    "scenario": title.split(":")[0].strip() if ":" in title else title,
                    "test": f"{spec.get('file', '')}::{title}",
                    "result": result,
                    "duration_s": round((last.get("duration") or 0) / 1000, 1),
                    "evidence": {"attachments": [a.get("name") for a in
                                                 last.get("attachments", [])]},
                    "failure_detail": mask("\n".join(errors))[-2500:],
                })
    return rows


def _md_cell(text: Any, limit: int = 220) -> str:
    s = text if isinstance(text, str) else json.dumps(text, default=str)
    s = " ".join(s.split())
    return (s[:limit] + "...") if len(s) > limit else s.replace("|", "\\|")


def build(backend: str, playwright: str, out_dir: str) -> dict[str, Any]:
    load_api_key()  # registers the key for masking
    rows = _backend_rows(backend) + _playwright_rows(playwright)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "summary": counts,
              "results": rows}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "real_world_report.json"), "w", encoding="utf-8") as fh:
        fh.write(mask(json.dumps(report, indent=2)))
    lines = [
        "# AgentVerse real-world scenario report", "",
        f"Generated {report['generated_at']} against the live local stack.", "",
        "Summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())), "",
        "| Scenario | Suite | Result | Duration (s) | Key evidence | Failure detail |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        ev = {k: v for k, v in (r.get("evidence") or {}).items() if k != "docker_logs"}
        detail = r.get("failure_detail") or ""
        detail = detail.strip().splitlines()[-1] if detail.strip() else ""
        lines.append(f"| {r['scenario']} | {r['suite']} | **{r['result']}** | "
                     f"{r.get('duration_s', '')} | {_md_cell(ev)} | {_md_cell(detail, 300)} |")
    lines += ["", "## Failure details", ""]
    for r in rows:
        if r["result"] in ("failed", "xfail", "error"):
            lines += [f"### {r['scenario']} ({r['result']})", "", "```",
                      (r.get("failure_detail") or "").strip()[-1800:], "```"]
            logs = (r.get("evidence") or {}).get("docker_logs") or []
            if logs:
                lines += ["", "Docker log evidence:", "", "```", *logs[:8], "```"]
            lines.append("")
    with open(os.path.join(out_dir, "real_world_report.md"), "w", encoding="utf-8") as fh:
        fh.write(mask("\n".join(lines)) + "\n")
    return report


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    rep = build(sys.argv[1], sys.argv[2], sys.argv[3])
    print("real-world report:", ", ".join(f"{k}={v}" for k, v in sorted(rep["summary"].items())))
