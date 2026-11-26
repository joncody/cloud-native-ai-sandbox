#!/usr/bin/env bash
# Automation script: Kind cluster bootstrap, image build/load, K8s rollout, and port-forwarding
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLUSTER_NAME="ai-sandbox"
NAMESPACE="ai-sandbox"

PF_PIDS=()
CLEANED_UP=0

cleanup() {
  if [ "$CLEANED_UP" -eq 1 ]; then
    return
  fi
  CLEANED_UP=1
  trap - INT TERM EXIT
  for pid in "${PF_PIDS[@]:-}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

trap cleanup INT TERM EXIT

resolve_host_gateway() {
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "host.docker.internal"
    return
  fi
  local gw
  gw="$(docker network inspect kind -f '{{range .IPAM.Config}}{{.Gateway}}{{"\n"}}{{end}}' 2>/dev/null | grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -n1 || true)"
  echo "${gw:-host.docker.internal}"
}

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "Creating Kind cluster '$CLUSTER_NAME'..."
  kind create cluster --name "$CLUSTER_NAME" --config "$ROOT_DIR/kind-config.yaml"
fi

echo "Building Go backend Docker image (Golang 1.26)..."
docker build -t go-backend:latest "$ROOT_DIR/app/"

echo "Building Python LangGraph AI agent Docker image..."
docker build -t ai-agent:latest "$ROOT_DIR/agent/"

echo "Loading container images into Kind cluster ('$CLUSTER_NAME')..."
kind load docker-image go-backend:latest --name "$CLUSTER_NAME"
kind load docker-image ai-agent:latest --name "$CLUSTER_NAME"

if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  echo "Creating namespace '$NAMESPACE'..."
  kubectl create namespace "$NAMESPACE"
fi

if ! kubectl get secret postgres-credentials -n "$NAMESPACE" >/dev/null 2>&1; then
  echo "Creating fallback secret 'postgres-credentials'..."
  kubectl create secret generic postgres-credentials \
    -n "$NAMESPACE" \
    --from-literal=username=postgres \
    --from-literal=password=postgres \
    --from-literal=database=aisandbox \
    --from-literal=url="postgresql://postgres:postgres@postgres-service:5432/aisandbox"
fi

echo "Deploying PostgreSQL vector database..."
kubectl apply -f "$ROOT_DIR/k8s/postgres.yaml"
kubectl rollout status deployment/postgres-deployment -n "$NAMESPACE" --timeout=60s

echo "Deploying Prometheus monitoring server..."
kubectl apply -f "$ROOT_DIR/k8s/prometheus.yaml"
kubectl rollout status deployment/prometheus-deployment -n "$NAMESPACE" --timeout=60s

HOST_GATEWAY="$(resolve_host_gateway)"
echo "Deploying API Gateway and AI Agent microservices (host gateway: $HOST_GATEWAY)..."
kubectl apply -f "$ROOT_DIR/k8s/deploy.yaml"
kubectl set env deployment/agent-deployment -n "$NAMESPACE" \
  OLLAMA_URL="http://${HOST_GATEWAY}:11434" \
  LOCALSTACK_URL="http://${HOST_GATEWAY}:4566"

echo "Waiting for application deployment rollouts..."
kubectl rollout status deployment/backend-deployment -n "$NAMESPACE"
kubectl rollout status deployment/agent-deployment -n "$NAMESPACE"

echo "Starting port forwarding for local API and Prometheus UI access..."
kubectl port-forward svc/backend-service 8080:8080 -n "$NAMESPACE" &
PF_PIDS+=("$!")
kubectl port-forward svc/prometheus-service 9090:9090 -n "$NAMESPACE" &
PF_PIDS+=("$!")

echo "==========================================================="
echo "Deployment Complete!"
echo "API Gateway Endpoint: http://localhost:8080/prompt"
echo "Prometheus Dashboard: http://localhost:9090"
echo "Press Ctrl-C to stop port-forwarding."
echo "==========================================================="

wait
