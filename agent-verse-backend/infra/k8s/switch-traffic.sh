#!/usr/bin/env bash
# switch-traffic.sh — Blue/Green traffic switcher for AgentVerse backend
# Usage:
#   ./switch-traffic.sh green   # switch traffic to green deployment
#   ./switch-traffic.sh blue    # switch traffic back to blue deployment
#   ./switch-traffic.sh status  # show current active color

set -euo pipefail

NAMESPACE="${NAMESPACE:-agentverse}"
TARGET_COLOR="${1:-}"

if [[ -z "$TARGET_COLOR" ]]; then
  echo "Usage: $0 <blue|green|status>" >&2
  exit 1
fi

CURRENT_COLOR=$(kubectl get service agentverse-backend -n "$NAMESPACE" \
  -o jsonpath='{.spec.selector.color}' 2>/dev/null || echo "unknown")

if [[ "$TARGET_COLOR" == "status" ]]; then
  echo "Current active color: $CURRENT_COLOR"
  echo ""
  echo "Blue replicas:  $(kubectl get deployment agentverse-backend-blue  -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
  echo "Green replicas: $(kubectl get deployment agentverse-backend-green -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
  exit 0
fi

if [[ "$TARGET_COLOR" != "blue" && "$TARGET_COLOR" != "green" ]]; then
  echo "Error: color must be 'blue' or 'green', got '$TARGET_COLOR'" >&2
  exit 1
fi

if [[ "$TARGET_COLOR" == "$CURRENT_COLOR" ]]; then
  echo "Traffic is already routing to $TARGET_COLOR. Nothing to do."
  exit 0
fi

# 1. Scale up the target color if it has 0 replicas
TARGET_REPLICAS=$(kubectl get deployment "agentverse-backend-${TARGET_COLOR}" \
  -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)

if [[ "$TARGET_REPLICAS" -eq 0 ]]; then
  echo "Scaling up $TARGET_COLOR deployment to 2 replicas..."
  kubectl scale deployment "agentverse-backend-${TARGET_COLOR}" \
    --replicas=2 -n "$NAMESPACE"
fi

# 2. Wait for target deployment to be ready
echo "Waiting for $TARGET_COLOR deployment to become ready..."
kubectl rollout status deployment/"agentverse-backend-${TARGET_COLOR}" \
  -n "$NAMESPACE" --timeout=120s

# 3. Verify health of new pods
echo "Verifying $TARGET_COLOR health endpoint..."
TARGET_POD=$(kubectl get pods -n "$NAMESPACE" \
  -l "app=agentverse-backend,color=${TARGET_COLOR}" \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [[ -n "$TARGET_POD" ]]; then
  HTTP_CODE=$(kubectl exec "$TARGET_POD" -n "$NAMESPACE" -- \
    curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
  if [[ "$HTTP_CODE" != "200" ]]; then
    echo "ERROR: Health check failed for $TARGET_COLOR pod ($HTTP_CODE). Aborting switch." >&2
    exit 1
  fi
fi

# 4. Switch service selector to target color
echo "Switching service traffic from $CURRENT_COLOR to $TARGET_COLOR..."
kubectl patch service agentverse-backend -n "$NAMESPACE" \
  --type='json' \
  -p="[{\"op\": \"replace\", \"path\": \"/spec/selector/color\", \"value\": \"${TARGET_COLOR}\"}]"

# Update annotation
kubectl annotate service agentverse-backend -n "$NAMESPACE" \
  "agentverse.ai/active-color=${TARGET_COLOR}" --overwrite

# 5. Scale down old color
echo "Scaling down $CURRENT_COLOR deployment..."
kubectl scale deployment "agentverse-backend-${CURRENT_COLOR}" \
  --replicas=0 -n "$NAMESPACE"

echo ""
echo "Traffic switched: $CURRENT_COLOR -> $TARGET_COLOR"
echo "  Service now routes to: $TARGET_COLOR"
echo ""
echo "  To roll back: ./switch-traffic.sh $CURRENT_COLOR"
