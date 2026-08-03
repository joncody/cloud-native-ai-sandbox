# Cloud-Native AI Sandbox

A local, zero-cost cloud-native platform demonstrating microservices, Retrieval-Augmented Generation (RAG) with **LangGraph** and **pgvector**, OpenTelemetry observability, **Prometheus** metrics, **LocalStack** cloud simulation, and local LLM orchestration.

---

## 🏗️ Architecture Overview

```
                          +-------------------+
                          |   curl / Client   |
                          +---------+---------+
                                    |
                                    v (POST /prompt)
                        +-----------+-----------+
                        |  Go API Gateway       |
                        |  - Golang 1.26        |
                        |  - Gin Web Framework  |
                        |  - OTEL Tracing       |
                        |  - Prometheus Metrics |
                        +-----------+-----------+
                                    |
                                    v (POST /process)
                        +-----------+-----------+
                        |  Python AI Agent      |
                        |  - LangGraph RAG Engine|
                        |  - Flask Microservice |
                        |  - Prometheus Metrics |
                        +-----+-----+-----+-----+
                              |     |     |
            +-----------------+     |     +-----------------+
            |                       v                       |
            v                +------+------+                v
   +--------+-------+        | LocalStack  |       +--------+-------+
   | PostgreSQL     |        | S3 Bucket   |       | Ollama Engine  |
   | + pgvector     |        +-------------+       | (Host: 11434)  |
   | (Vector Store) |                              +----------------+
   +----------------+
            ^
            | (Scrapes /metrics)
   +--------+-------+
   | Prometheus     |
   | Monitoring     |
   +----------------+
```

### Components & Microservices

1. **Go API Gateway (`app/main.go`)**:
   - Built with **Golang 1.26** and **Gin**.
   - Integrates **OpenTelemetry** stdout trace exporting and **Prometheus** HTTP metrics (`http_requests_total`, `http_request_duration_seconds`).
   - Listens on port `8080` and exposes `POST /prompt`, `GET /health`, and `GET /metrics`.

2. **Python LangGraph AI Agent (`agent/agent.py`)**:
   - Built with **Flask** and **LangGraph**.
   - Executes a stateful 3-node RAG graph workflow:
     - **`embed_and_retrieve`**: Embeds incoming prompts using Ollama and queries PostgreSQL `pgvector` for vector similarity matches (`<=>` cosine distance operator) to construct contextual memory.
     - **`generate`**: Invokes the `llama3` model on Ollama with prompt + retrieved RAG context.
     - **`persist`**: Stores text response files in LocalStack S3 (`ai-agent-storage` bucket) and inserts prompt, response, and 4096-dimensional vector embeddings into PostgreSQL.
   - Includes automatic database schema initialization with connection retry logic.
   - Exposes Prometheus metrics (`agent_requests_total`, `agent_graph_node_latency_seconds`) on port `5000`.

3. **Infrastructure & Observability**:
   - **PostgreSQL (`pgvector/pgvector:pg16`)**: In-cluster vector similarity database running on port `5432`.
   - **Prometheus (`prom/prometheus:v2.51.0`)**: In-cluster monitoring server configured via ConfigMap to scrape `/metrics` from both microservices every 5 seconds.
   - **LocalStack (`3.8.1`)**: Simulated AWS environment running community image on host port `4566`.
   - **Ollama**: Host-based local LLM execution engine running on host port `11434` (`llama3`).
   - **Kind Cluster**: Kubernetes v1.29.2 cluster (`ai-sandbox`).
   - **OpenTofu / Terraform**: Declarative infrastructure configs for namespaces, S3 buckets, and Kubernetes secrets.

---

## 📁 Repository Structure

```text
cloud-native-ai-sandbox/
├── agent/
│   ├── agent.py            # Flask microservice & LangGraph RAG graph workflow
│   ├── Dockerfile          # Python 3.11-slim container build definition
│   └── requirements.txt    # Python dependencies (LangGraph, pgvector, boto3, etc.)
├── app/
│   ├── main.go             # Go API Gateway with OTEL & Prometheus
│   ├── go.mod              # Go 1.26 module definition
│   └── Dockerfile          # Multi-stage Go 1.26 build definition
├── k8s/
│   ├── deploy.yaml         # Deployments & Services for API Gateway and AI Agent
│   ├── postgres.yaml       # Deployment & Service for PostgreSQL + pgvector
│   └── prometheus.yaml     # ConfigMap, Deployment & NodePort for Prometheus
├── terraform/
│   ├── main.tf             # Terraform resources for Namespace, S3, and Secrets
│   └── providers.tf        # Provider definitions for LocalStack AWS and Kubernetes
├── scripts/
│   └── run.sh              # Build, image loading, rollout verification & port-forwarding
├── kind-config.yaml        # Kind cluster creation configuration
├── .gitignore              # Git ignore rules for Go, Python, and Terraform artifacts
└── README.md               # Architecture and documentation guide
```

---

## 📋 Prerequisites

- **Ubuntu Linux** host system
- **Docker Engine** (version 24.0+)
- **Kind** (version v1.29.2+)
- **kubectl**
- **Ollama** service running on host port `11434` with model pulled (`ollama pull llama3`)
- **AWS CLI**

---

## 🚀 Setup & Deployment Guide

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

Apply firewall rules and restart Docker daemon:

```bash
sudo nft -f /etc/nftables.conf
sudo systemctl restart docker
```

> **Note:** Restarting the Docker service immediately after reloading `nftables` is required so Docker can recreate its custom dynamic NAT and container routing iptables chains (`DOCKER` / `POSTROUTING`).

---

### 2. Launch LocalStack & Create S3 Bucket

Launch LocalStack AWS simulation on host port `4566` and create the storage bucket:

```bash
docker rm -f localstack
docker run -d --name localstack -p 0.0.0.0:4566:4566 localstack/localstack:3.8.1

# Wait for LocalStack initialization
sleep 5

AWS_ACCESS_KEY_ID=mock_key AWS_SECRET_ACCESS_KEY=mock_secret AWS_DEFAULT_REGION=us-east-1 \
  aws --endpoint-url=http://localhost:4566 s3 mb s3://ai-agent-storage || true
```

---

### 3. Build & Deploy Microservices

Run the automated build and deployment script:

```bash
cd scripts
./run.sh
```

---

## 🧪 Verification & Testing

### 1. Seed Vector Database with Initial Prompt

Send an initial prompt to generate the first response and persist embeddings in `pgvector`:

```bash
curl -X POST http://localhost:8080/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain what Kubernetes pods are in detail."}'
```
*Response will show `"context_retrieved": false` on the first query.*

### 2. Test LangGraph RAG Context Retrieval

Send a follow-up query related to the first prompt. LangGraph will perform vector search in `pgvector` and inject previous knowledge into the prompt context:

```bash
curl -X POST http://localhost:8080/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "How do containers inside the same pod communicate with each other?"}'
```
*Response will now show `"context_retrieved": true`.*

### 3. Inspect PostgreSQL Vector Database Records

Verify the embeddings stored inside the PostgreSQL container:

```bash
kubectl exec -it deployment/postgres-deployment -n ai-sandbox -- \
  psql -U postgres -d aisandbox -c "SELECT id, prompt, left(response, 40) as preview, created_at FROM embeddings_store;"
```

### 4. Verify LocalStack S3 Storage

List files created in the simulated S3 bucket:

```bash
AWS_ACCESS_KEY_ID=mock_key AWS_SECRET_ACCESS_KEY=mock_secret AWS_DEFAULT_REGION=us-east-1 \
  aws --endpoint-url=http://localhost:4566 s3 ls s3://ai-agent-storage/
```

---

## 📊 Observability & Metrics

### Prometheus Dashboard
Open **[http://localhost:9090](http://localhost:9090)** in your browser to query live metrics.

Key metrics available:
- `http_requests_total`: Request counter for the Go API Gateway (labeled by status code).
- `http_request_duration_seconds_bucket`: Latency histogram for HTTP requests.
- `agent_requests_total`: Total prompt requests handled by the LangGraph agent.
- `agent_graph_node_latency_seconds_bucket`: Execution latency broken down per LangGraph workflow node (`embed_and_retrieve`, `generate`, `persist`).

### Query Metrics via CLI
```bash
curl -s http://localhost:8080/metrics | grep http_requests_total
```
