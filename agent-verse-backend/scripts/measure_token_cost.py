#!/usr/bin/env python3
"""
Token/cost measurement harness.
Runs a golden-goal set and measures tokens before/after Phase 2 optimizations.

Usage:
  uv run python scripts/measure_token_cost.py --goals scripts/golden_goals.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


GOLDEN_GOALS = [
    "Find all open JIRA tickets in project BAU assigned to harsh",
    "Summarize the last 5 GitHub PRs in the main repository",
    "List all active agents and their current status",
    "Generate a weekly sprint status report for team Atlas",
    "Find security vulnerabilities in PR #42",
]


def measure_token_cost(goals: list[str]) -> dict:
    """Measure estimated token count for each goal's prompt."""
    try:
        from app.agent.tokenizer import Tokenizer
        tokenizer = Tokenizer()
    except Exception as e:
        print(f"Tokenizer unavailable: {e}")
        return {}

    results = {}
    total_tokens = 0

    for goal in goals:
        # Estimate tokens for a typical planner prompt
        sample_prompt = f"""You are a planning agent.
Goal: {goal}

Available tools: [tool list placeholder]

Generate a step-by-step plan as JSON: {{"steps": [...]}}"""

        tokens = tokenizer.count(sample_prompt)
        total_tokens += tokens
        results[goal[:50]] = {"tokens": tokens, "accurate": tokenizer.is_accurate}

    results["__summary__"] = {
        "total_tokens": total_tokens,
        "goals": len(goals),
        "avg_tokens_per_goal": total_tokens // max(len(goals), 1),
        "tokenizer_accurate": tokenizer.is_accurate,
    }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure token cost on golden goals")
    parser.add_argument("--goals", help="JSON file with goal list")
    parser.add_argument("--output", help="Output JSON file", default="token_costs.json")
    args = parser.parse_args()

    goals = GOLDEN_GOALS
    if args.goals:
        try:
            with open(args.goals) as f:
                goals = json.load(f)
        except Exception as e:
            print(f"Failed to load goals file: {e}")

    results = measure_token_cost(goals)

    summary = results.get("__summary__", {})
    print(f"\nToken Cost Measurement")
    print(f"=" * 40)
    print(f"Goals measured: {summary.get('goals', 0)}")
    print(f"Total tokens: {summary.get('total_tokens', 0):,}")
    print(f"Avg tokens/goal: {summary.get('avg_tokens_per_goal', 0):,}")
    print(f"Using tiktoken: {summary.get('tokenizer_accurate', False)}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
