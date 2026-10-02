"""Build the real-world JSON + markdown report.

    python -m tests.real_world.report <backend-results.jsonl> <playwright.json> <out-dir>

Writes ``<out-dir>/real_world_report.json`` and ``<out-dir>/real_world_report.md``:
a per-scenario verdict (parametrized tests rolled up, skip reasons spelled out), a
metrics table (hit@k, answer accuracy, latencies, throughput, cost, per-strategy
quality) and one row per test (result, duration, key evidence, failure detail).
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


def scenario_rollup(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One verdict per scenario across its tests (parametrized formats/strategies):
    failed if any test failed, passed if any passed (the rest skipped), else skipped."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = out.setdefault(r["scenario"], {"passed": 0, "failed": 0, "skipped": 0,
                                           "xfail": 0, "tests": 0, "skip_reasons": []})
        s["tests"] += 1
        key = r["result"] if r["result"] in ("passed", "failed", "skipped", "xfail") else "failed"
        s[key] += 1
        if r["result"] == "skipped" and r.get("failure_detail"):
            s["skip_reasons"].append(_skip_reason(r["failure_detail"]))
    for s in out.values():
        s["result"] = ("failed" if s["failed"] else "passed" if s["passed"]
                       else "xfail" if s["xfail"] else "skipped")
        s["skip_reasons"] = sorted(set(s["skip_reasons"]))
    return out


def _skip_reason(detail: str) -> str:
    text = " ".join(str(detail).split())
    return text.split("Skipped: ", 1)[-1][:300]


def _metric_cells(metrics: dict[str, Any]) -> list[str]:
    """Flatten one level: {"a": 1, "lat": {"p50": 2}} -> ["a=1", "lat.p50=2"]."""
    cells: list[str] = []
    for k, v in metrics.items():
        if isinstance(v, dict) and k != "strategies":
            cells.extend(f"{k}.{k2}={v2}" for k2, v2 in v.items() if not isinstance(v2, dict))
        elif k != "strategies":
            cells.append(f"{k}={v}")
    return cells


def _strategy_table(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for r in rows:
        strategies = ((r.get("evidence") or {}).get("metrics") or {}).get("strategies")
        if not strategies:
            continue
        lines += ["", f"### RAG strategies ({r['test']})", "",
                  "| Strategy | Available | hit@5 | MRR | Answer acc. | Citation acc. | "
                  "p50 ms | p95 ms | Errors / reason |", "|---|---|---|---|---|---|---|---|---|"]
        for sid, m in sorted(strategies.items()):
            if m.get("available"):
                lines.append(f"| {sid} | yes | {m.get('hit_at_5')} | {m.get('mrr')} | "
                             f"{m.get('answer_accuracy')} | {m.get('citation_accuracy')} | "
                             f"{m.get('p50_ms')} | {m.get('p95_ms')} | {m.get('errors')} |")
            else:
                lines.append(f"| {sid} | no | | | | | | | {_md_cell(m.get('reason'), 80)} "
                             f"(probe HTTP {m.get('probe_http')}) |")
    return lines


def build(backend: str, playwright: str, out_dir: str) -> dict[str, Any]:
    load_api_key()  # registers the key for masking
    rows = _backend_rows(backend) + _playwright_rows(playwright)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    rollup = scenario_rollup(rows)
    metrics = {r["test"]: (r.get("evidence") or {}).get("metrics") for r in rows
               if (r.get("evidence") or {}).get("metrics")}
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "summary": counts,
              "scenarios": rollup, "metrics": metrics, "results": rows}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "real_world_report.json"), "w", encoding="utf-8") as fh:
        fh.write(mask(json.dumps(report, indent=2)))
    verdicts: dict[str, int] = {}
    for s in rollup.values():
        verdicts[s["result"]] = verdicts.get(s["result"], 0) + 1
    lines = [
        "# AgentVerse real-world scenario report", "",
        f"Generated {report['generated_at']} against the live local stack.", "",
        "Tests: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
        "Scenarios: " + ", ".join(f"{k}={v}" for k, v in sorted(verdicts.items())), "",
        "## Scenarios", "",
        "| Scenario | Result | Tests (pass/fail/skip) | Skip reason |", "|---|---|---|---|",
    ]
    for name, s in sorted(rollup.items()):
        lines.append(f"| {name} | **{s['result']}** | {s['passed']}/{s['failed']}/{s['skipped']}"
                     f" | {_md_cell('; '.join(s['skip_reasons']), 260)} |")
    lines += ["", "## Metrics", "", "| Scenario | Test | Metrics |", "|---|---|---|"]
    for r in rows:
        m = (r.get("evidence") or {}).get("metrics")
        if m:
            lines.append(f"| {r['scenario']} | {_md_cell(r['test'].split('::')[-1], 60)} | "
                         f"{_md_cell(', '.join(_metric_cells(m)), 600)} |")
    lines += _strategy_table(rows)
    lines += ["", "## Tests", "",
              "| Scenario | Suite | Result | Duration (s) | Key evidence | Failure detail |",
              "|---|---|---|---|---|---|"]
    for r in rows:
        ev = {k: v for k, v in (r.get("evidence") or {}).items()
              if k not in ("docker_logs", "metrics")}
        detail = r.get("failure_detail") or ""
        detail = detail.strip().splitlines()[-1] if detail.strip() else ""
        lines.append(f"| {r['scenario']} | {r['suite']} | **{r['result']}** | "
                     f"{r.get('duration_s', '')} | {_md_cell(ev)} | {_md_cell(detail, 300)} |")
    lines += ["", "## Failure details", ""]
    for r in rows:
        if r["result"] in ("failed", "xfail", "error"):
            lines += [f"### {r['scenario']} ({r['result']})", "", f"`{r['test']}`", "", "```",
                      (r.get("failure_detail") or "").strip()[-1800:], "```"]
            logs = (r.get("evidence") or {}).get("docker_logs") or []
            if logs:
                lines += ["", "Docker log evidence:", "", "```", *logs[:8], "```"]
            lines.append("")
    with open(os.path.join(out_dir, "real_world_report.md"), "w", encoding="utf-8") as fh:
        fh.write(mask("\n".join(lines)) + "\n")
    return report


def print_summary(report: dict[str, Any]) -> None:
    """Per-scenario PASS/FAIL/SKIP lines for the terminal (masked)."""
    width = max((len(n) for n in report["scenarios"]), default=10)
    for name, s in sorted(report["scenarios"].items()):
        reason = f"  ({s['skip_reasons'][0][:110]})" if s["result"] == "skipped" and \
            s["skip_reasons"] else ""
        print(mask(f"  {s['result'].upper():8} {name:<{width}}  "
                   f"{s['passed']}/{s['failed']}/{s['skipped']}{reason}"))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    rep = build(sys.argv[1], sys.argv[2], sys.argv[3])
    print("real-world report:", ", ".join(f"{k}={v}" for k, v in sorted(rep["summary"].items())))
    print_summary(rep)
