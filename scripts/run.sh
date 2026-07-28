#!/bin/bash
# Automation build, image loading, Kubernetes deployment, rollout check, and port-forwarding
set -e

echo "Building Go backend image..."
docker build -t go-backend:latest ../app/

echo "Building Python agent image..."
docker build -t ai-agent:latest ../agent/

echo "Loading images into Kind cluster..."
kind load docker-image go-backend:latest --name ai-sandbox
kind load docker-image ai-agent:latest --name ai-sandbox

echo "Ensuring namespace exists..."
kubectl create namespace ai-sandbox --dry-run=client -o yaml | kubectl apply -f -

echo "Deploying applications to Kubernetes..."
kubectl apply -f ../k8s/deploy.yaml

echo "Waiting for deployments to be ready..."
kubectl rollout status deployment/backend-deployment -n ai-sandbox
kubectl rollout status deployment/agent-deployment -n ai-sandbox

echo "Forwarding port to access the API..."
kubectl port-forward svc/backend-service 8080:8080 -n ai-sandbox
