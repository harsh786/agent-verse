# Code sandbox runner

Workflow `code` steps, `POST /tools/execute-code` and chat code blocks run tenant
code. In the shipped deployments that code runs in the **code-sandbox runner**
(`agent-verse-backend/app/sandbox/runner.py`), its own container that the API and the
workers call over the internal network. The app containers have no Docker daemon,
and you must not give them the host's Docker socket: any tenant's code could then
take root on the host.

## How a program is run

`app/tools/code_interpreter.py` picks where each execution runs:

1. `CODE_SANDBOX_URL` is set: the remote runner, authenticated with
   `CODE_SANDBOX_TOKEN`. When the runner is configured it is the only option. If it
   is down, the execution fails and says why. It never falls back to Docker or the
   host.
2. A Docker daemon is reachable: a throw-away container per execution (local
   development).
3. `ENVIRONMENT=development|test` and `AGENTVERSE_ALLOW_SUBPROCESS_EXEC=true`: a host
   subprocess. This is not sandboxed and is for development only.
4. Otherwise the execution fails with an error that explains how to enable a sandbox.

Each execution in the runner gets:

* a fresh process, started in a new session so the whole process group can be
  killed;
* its own unprivileged UID/GID per concurrent slot (uid 20000+). It cannot read
  another execution's files or `/proc` entries, and cannot signal another execution
  or the runner. After each run every process of that UID is killed and its workdir
  deleted before the UID is reused. If that cleanup fails, the slot is quarantined;
  when every slot is quarantined, `/healthz` returns 503 and the pod is restarted;
* a minimal environment built from scratch, so no platform secrets and not the
  runner's token;
* rlimits for CPU seconds, address space (`CODE_SANDBOX_MEMORY_MB`, default 256),
  file size, open files, processes, and no core dumps; a wall-clock timeout that
  kills the group (exit 124); and a 1 MB cap per output stream (the program is
  killed at the first byte over it);
* a private `0700` workdir on the `/sandbox` tmpfs. The root filesystem is read-only.

The network boundary comes from the deployment. In compose the runner sits only on
the `internal: true` `code-sandbox` network. In Kubernetes its NetworkPolicy denies
all egress, DNS included, and accepts connections only from the API, worker and
sub-goal worker pods. Enforcing that policy needs a CNI that supports
NetworkPolicy.

The container starts as root only so it can switch UIDs. It drops every capability
except `SETUID`, `SETGID` and `KILL`, and runs with `no-new-privileges`, a read-only
root filesystem and no service-account token.

## Deploy

**Compose** (`infra/docker-compose.yml`, `infra/docker-compose.prod.yml`): the
`code-sandbox` service builds from `Dockerfile.sandbox`, which is `python:3.12-slim`
plus the one runner file with no pip installs. The API, `worker`, `subgoal-worker`
and `workflow-worker` get `CODE_SANDBOX_URL=http://code-sandbox:8080` and
`CODE_SANDBOX_TOKEN`. In production, set `CODE_SANDBOX_TOKEN` (at least 16
characters) in the environment. The prod file refuses to start without it.

```bash
docker-compose -f infra/docker-compose.yml up -d --build code-sandbox worker workflow-worker subgoal-worker backend
```

**Helm**: `codeSandbox.enabled: true` is the default in both charts.

* `infra/helm/agentverse`: build the image with
  `docker build -f Dockerfile.sandbox -t agentverse/code-sandbox:local .` and set
  `secrets.codeSandboxToken`.
* `helm/agentverse`: push `<imageRegistry>/code-sandbox:<tag>` and provide
  `agentverse/code-sandbox-token` in the secret store (`externalSecrets.secrets.codeSandboxToken`).

To skip building a separate image, use the backend image with
`codeSandbox.command: ["python", "-m", "app.sandbox.runner"]`. The trade-off is that
programs can then read the app's source files.

**Check it:** `kubectl exec deploy/<release>-worker -- python -c "import urllib.request;
print(urllib.request.urlopen('http://<release>-agentverse-code-sandbox:8080/healthz').read())"`
should report `"isolation": "uid"`. Then run a workflow with a `code` step.

Tuning (runner env): `CODE_SANDBOX_MAX_CONCURRENCY` (slots per pod; extra requests
wait up to `CODE_SANDBOX_QUEUE_TIMEOUT_SECONDS`, then get 429),
`CODE_SANDBOX_MEMORY_MB`, `CODE_SANDBOX_MAX_TIMEOUT_SECONDS`,
`CODE_SANDBOX_MAX_OUTPUT_BYTES`. To scale, add replicas rather than raising
concurrency.

**Troubleshooting:** the error text shows up in the step's error and in the run
banner.

* `code sandbox error: runner at … is unreachable`: the runner is down, or the
  caller is not attached to the `code-sandbox` network or not allowed by the
  NetworkPolicy.
* `rejected the credentials`: the app's `CODE_SANDBOX_TOKEN` differs from the
  runner's.
* `No code sandbox available`: `CODE_SANDBOX_URL` is not set on that service.
