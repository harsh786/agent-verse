# Runbook: High Goal Failure Rate

## Alert
`HighGoalFailureRate` — Goal failure rate > 10% over 5 minutes.

## Severity
Warning

## Diagnosis Steps
1. Check backend logs: `kubectl logs -n agentverse deployment/agentverse-backend --tail=100`
2. Check for LLM provider errors: `kubectl logs -n agentverse deployment/agentverse-backend | grep "anthropic\|openai\|provider"`
3. Check Celery worker health: `kubectl get pods -n agentverse -l app=agentverse-worker`
4. Query failed goals: `GET /goals?status=failed&limit=20` with admin API key
5. Check cost controller: `GET /governance/budget` — budget exhaustion causes failures

## Resolution
- LLM provider down: switch provider via `PUT /tenants/me/llm`
- Budget exhausted: `PUT /governance/budget` with higher limits
- Worker overloaded: scale workers `kubectl scale deployment agentverse-worker --replicas=5`
