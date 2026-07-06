# Local GitHub Self-Hosted Runner for Minikube Deployments

GitHub-hosted runners cannot reach your laptop's Minikube cluster. To deploy to
local Minikube from GitHub Actions, run a **self-hosted runner** on the same
machine that runs Minikube.

This repository expects a local runner with labels:

```text
self-hosted, macOS, ARM64, agentverse-local, minikube
```

## What Was Installed Locally

Runner directory:

```bash
~/actions-runner/agent-verse
```

Registered runner name pattern:

```text
agentverse-minikube-<hostname>-arm64
```

## Manual Install / Reinstall

```bash
mkdir -p ~/actions-runner/agent-verse
cd ~/actions-runner/agent-verse

LATEST=$(gh api repos/actions/runner/releases/latest --jq '.tag_name')
VERSION=${LATEST#v}
curl -fsSL -o actions-runner-osx-arm64-${VERSION}.tar.gz \
  https://github.com/actions/runner/releases/download/${LATEST}/actions-runner-osx-arm64-${VERSION}.tar.gz
tar xzf actions-runner-osx-arm64-${VERSION}.tar.gz

TOKEN=$(gh api -X POST repos/harsh786/agent-verse/actions/runners/registration-token --jq '.token')
./config.sh \
  --url https://github.com/harsh786/agent-verse \
  --token "$TOKEN" \
  --name "agentverse-minikube-$(hostname)-arm64" \
  --labels "agentverse-local,minikube,macos,arm64" \
  --unattended \
  --replace
```

Start the runner in the foreground:

```bash
cd ~/actions-runner/agent-verse
./run.sh
```

Start it in the background:

```bash
cd ~/actions-runner/agent-verse
nohup ./run.sh > runner.log 2>&1 &
echo $! > runner.pid
```

Verify from GitHub CLI:

```bash
gh api repos/harsh786/agent-verse/actions/runners \
  --jq '.runners[] | select(.name|contains("agentverse-minikube")) | {name,status,busy,labels:[.labels[].name]}'
```

## Workflows That Use This Runner

| Workflow | Purpose |
|---|---|
| `.github/workflows/deploy-local-minikube.yml` | Full local build + Helm deploy into Minikube |
| `.github/workflows/local-minikube-ci-cd.yml` | Local CI + all-in-one / infra-only / app-only / observability-only deploy modes |

## Required Local Tools

The runner machine must have:

```text
gh
git
docker
minikube
kubectl
helm
uv
node + npm
```

## Recommended Local Minikube Profile

```bash
minikube -p agentverse start --cpus=4 --memory=7600 --disk-size=60g
kubectl config use-context agentverse
```

If your existing Minikube profile has fewer resources, Minikube may refuse to
resize it. Delete only if you are okay losing local cluster state:

```bash
minikube -p agentverse delete
minikube -p agentverse start --cpus=4 --memory=7600 --disk-size=60g
```

## Running Local Workflows

From GitHub UI:

```text
Actions → Local Minikube Full Deploy → Run workflow
```

or:

```text
Actions → Local Minikube CI/CD Matrix → Run workflow
mode = ci-only | all-in-one | infra-only | app-only | observability-only
```

## Known Local Resource Constraint

The full backend image with Playwright browser binaries is large. For Minikube
smoke tests, workflows default to:

```bash
--build-arg INSTALL_PLAYWRIGHT=false
```

Use `install_playwright=true` only when validating RPA/browser automation.
