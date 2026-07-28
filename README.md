# Cloud-Native AI Sandbox

A local, zero-cost cloud-native platform demonstrating microservices, Infrastructure as Code (IaC), Kubernetes, OpenTelemetry observability, LocalStack cloud simulation, and local LLM orchestration.

## Architecture Overview

The platform consists of two microservices running inside a Kind Kubernetes cluster interacting with host-based LocalStack and Ollama services:

1. **Go API Gateway (`app/main.go`)**:
   - Built with Gin and OpenTelemetry stdout tracing.
   - Listens on port 8080 and exposes the `POST /prompt` endpoint.
   - Forwards JSON payloads to the Python AI agent service.

2. **Python AI Agent (`agent/agent.py`)**:
   - Listens on port 5000 inside the cluster.
   - Queries Ollama on host port 11434 (`llama3` model).
   - Stores generated text responses in LocalStack S3 (`ai-agent-storage` bucket) on host port 4566.

3. **Infrastructure & Host Services**:
   - **Kind Cluster**: Kubernetes v1.29.2 cluster (`ai-sandbox`).
   - **LocalStack**: AWS simulation running community image `3.8.1` on port 4566.
   - **Ollama**: Local LLM execution engine running on host port 11434.
   - **OpenTofu / Terraform**: Manages Kubernetes namespace and S3 bucket provisioning.

---

## Repository Structure

- `agent/`: Python Flask microservice, Dockerfile, and requirements.txt.
- `app/`: Go Gin API gateway microservice (Golang 1.26), Dockerfile, and OpenTelemetry setup.
- `k8s/`: Kubernetes Deployment and Service manifests (`deploy.yaml`).
- `terraform/`: OpenTofu / Terraform infrastructure configurations.
- `kind-config.yaml`: Kind cluster creation configuration.
- `scripts/run.sh`: Automated build, cluster image loading, deployment rollout, and port-forwarding script.

---

## Prerequisites

- Ubuntu Linux host system
- Docker Engine (version 24.0+)
- Kind (version v1.29.2)
- kubectl
- Ollama service running on host port 11434 (`ollama pull llama3`)
- AWS CLI

---

## Setup & Execution Guide

### 1. Firewall Configuration (nftables)

Permit incoming TCP traffic from Docker network subnets in `/etc/nftables.conf`:

```nftables
	chain INPUT {
		type filter hook input priority filter; policy drop;

		# Trust loopback and Docker interfaces
		iifname "lo" accept
		iifname "docker0" accept
		iifname "br-*" accept
	}
```

Apply firewall rules:

```bash
sudo nft -f /etc/nftables.conf
```

### 2. Launch LocalStack (Community 3.8.1)

```bash
docker rm -f localstack
docker run -d --name localstack -p 0.0.0.0:4566:4566 localstack/localstack:3.8.1
```

### 3. Create S3 Bucket

```bash
AWS_ACCESS_KEY_ID=mock_key AWS_SECRET_ACCESS_KEY=mock_secret AWS_DEFAULT_REGION=us-east-1 \
  aws --endpoint-url=http://localhost:4566 s3 mb s3://ai-agent-storage
```

### 4. Build and Deploy Microservices

```bash
cd scripts
./run.sh
```

### 5. Send Prompt API Request

```bash
curl -X POST http://localhost:8080/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain Kubernetes in one sentence."}'
```

---

## Observability

The Go API gateway exports distributed trace spans to stdout using OpenTelemetry. Request metrics and trace IDs are recorded per invocation.
