"""Analyze coverage gaps for targeted test writing."""
import json

with open('/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/coverage.json') as f:
    d = json.load(f)

files = d['files']

# Show missing lines for the worst modules
targets = {
    'app/main.py': 'main',
    'app/perception/browser_agent.py': 'perception/browser_agent',
    'app/perception/multimodal.py': 'perception/multimodal',
    'app/scaling/tasks.py': 'scaling/tasks',
    'app/scaling/celery_app.py': 'scaling/celery_app',
    'app/rpa/executor.py': 'rpa/executor',
    'app/rpa/session_manager.py': 'rpa/session_manager',
    'app/services/goal_service.py': 'services/goal_service',
    'app/agent/graph.py': 'agent/graph',
    'app/api/enterprise.py': 'api/enterprise',
}

for path, label in targets.items():
    info = files.get(path, {})
    if not info:
        print(f"\n=== {label} === NOT FOUND in coverage data")
        continue
    pct = info['summary']['percent_covered']
    miss = info.get('missing_lines', [])
    print(f"\n=== {label} === {pct:.1f}% ({len(miss)} missing)")
    if len(miss) <= 30:
        print(f"  Missing: {miss}")
    else:
        # Show ranges
        print(f"  First 25: {miss[:25]}")
        print(f"  Last 10: {miss[-10:]}")
