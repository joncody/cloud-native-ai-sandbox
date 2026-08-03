#!/bin/bash
# Automation build, image loading, Kubernetes deployment, rollout verification, and port forwarding script
set -e

echo "Building Go backend Docker image (Golang 1.26)..."
docker build -t go-backend:latest ../app/

echo "Building Python LangGraph AI agent Docker image..."
docker build -t ai-agent:latest ../agent/

echo "Loading container images into Kind cluster ('ai-sandbox')..."
kind load docker-image go-backend:latest --name ai-sandbox
kind load docker-image ai-agent:latest --name ai-sandbox

echo "Ensuring target namespace ('ai-sandbox') exists..."
kubectl create namespace ai-sandbox --dry-run=client -o yaml | kubectl apply -f -

echo "Deploying PostgreSQL vector database..."
kubectl apply -f ../k8s/postgres.yaml
kubectl rollout status deployment/postgres-deployment -n ai-sandbox --timeout=60s

echo "Deploying Prometheus monitoring server..."
kubectl apply -f ../k8s/prometheus.yaml
kubectl rollout status deployment/prometheus-deployment -n ai-sandbox --timeout=60s

echo "Deploying API Gateway and AI Agent microservices..."
kubectl apply -f ../k8s/deploy.yaml

echo "Waiting for application deployment rollouts..."
kubectl rollout status deployment/backend-deployment -n ai-sandbox
kubectl rollout status deployment/agent-deployment -n ai-sandbox

echo "Starting port forwarding for local API and Prometheus UI access..."
kubectl port-forward svc/backend-service 8080:8080 -n ai-sandbox &
kubectl port-forward svc/prometheus-service 9090:9090 -n ai-sandbox &

echo "==========================================================="
echo "Deployment Complete!"
echo "API Gateway Endpoint: http://localhost:8080/prompt"
echo "Prometheus Dashboard: http://localhost:9090"
echo "==========================================================="

wait
