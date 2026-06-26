# Runbook: Backend Instance Down

## Alert
`BackendInstanceDown` — Backend Prometheus target unreachable for 1 minute.

## Severity
Critical

## Diagnosis Steps
1. Check pod status: `kubectl get pods -n agentverse -l app=agentverse-backend`
2. Check pod logs: `kubectl logs -n agentverse <pod-name> --previous`
3. Check events: `kubectl describe pod -n agentverse <pod-name>`
4. Check resource limits: `kubectl top pods -n agentverse`

## Resolution
- OOMKilled: increase memory limit in backend-deployment.yaml
- CrashLoopBackOff: check logs for startup errors, verify env vars are set
- Readiness probe failing: check `/health` endpoint manually
