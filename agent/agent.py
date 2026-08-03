"""
AI Agent Microservice utilizing Flask, LangGraph, pgvector, and Prometheus.

This service implements a stateful RAG (Retrieval-Augmented Generation) workflow:
1. Receives prompt via HTTP POST.
2. Embeds the prompt and retrieves context from PostgreSQL (pgvector).
3. Invokes Ollama (llama3 model) on the host system.
4. Persists AI text output to LocalStack S3 and saves vector embeddings to PostgreSQL.
5. Exposes Prometheus metrics for scrapers.
"""

import os
import uuid
import time
import json
import boto3
import requests
import psycopg2
from psycopg2.extras import execute_values
from pgvector.psycopg2 import register_vector
from flask import Flask, request, jsonify, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from typing import TypedDict, List, Optional
from langgraph.graph import StateGraph, END

# Initialize Flask Application
app = Flask(__name__)

# ==============================================================================
# PROMETHEUS METRICS CONFIGURATION
# ==============================================================================
# Metric 1: Counter tracking total API requests handled by status
REQUEST_COUNT = Counter(
    "agent_requests_total",
    "Total prompt processing requests handled by the Python AI agent",
    ["status"]
)

# Metric 2: Histogram tracking execution latency for each LangGraph node
GRAPH_NODE_LATENCY = Histogram(
    "agent_graph_node_latency_seconds",
    "Latency distribution per LangGraph workflow node in seconds",
    ["node"]
)

# ==============================================================================
# ENVIRONMENT & CLIENT INITIALIZATION
# ==============================================================================
# Service URLs targeting host network bridge or internal cluster DNS
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://172.19.0.1:11434")
LOCALSTACK_URL = os.getenv("LOCALSTACK_URL", "http://172.19.0.1:4566")
BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "ai-agent-storage")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://postgres:postgres@postgres-service:5432/aisandbox")

# Initialize boto3 S3 client targeted at LocalStack AWS simulation endpoint
s3_client = boto3.client(
    "s3",
    region_name="us-east-1",
    aws_access_key_id="mock_key",
    aws_secret_access_key="mock_secret",
    endpoint_url=LOCALSTACK_URL,
)

# ==============================================================================
# DATABASE MANAGEMENT & RETRY LOGIC
# ==============================================================================
def get_db_conn():
    """Establish connection to PostgreSQL and register the pgvector extension type."""
    conn = psycopg2.connect(POSTGRES_URL)
    register_vector(conn)
    return conn

def init_db():
    """
    Ensure pgvector extension and the embeddings_store table exist in PostgreSQL.
    Includes a retry mechanism to handle container startup race conditions.
    """
    retries = 10
    for i in range(retries):
        try:
            conn = get_db_conn()
            with conn.cursor() as cur:
                # Enable vector extension if not already present
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                # Create table to store text prompts, AI responses, and 4096-dim vector embeddings
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS embeddings_store (
                        id UUID PRIMARY KEY,
                        prompt TEXT NOT NULL,
                        response TEXT NOT NULL,
                        embedding vector(4096),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
            conn.commit()
            conn.close()
            print("Database and pgvector schema initialized successfully.")
            return
        except Exception as e:
            print(f"Database init attempt {i+1}/{retries} failed: {e}. Retrying in 2 seconds...")
            time.sleep(2)

# Run schema initialization on module load
init_db()

# ==============================================================================
# LANGGRAPH STATE SCHEMA & WORKFLOW NODES
# ==============================================================================
class AgentState(TypedDict):
    """Defines the internal state passed between LangGraph workflow nodes."""
    prompt: str
    embedding: List[float]
    context: str
    ai_response: str
    saved_file: str

def embed_and_retrieve_node(state: AgentState) -> AgentState:
    """
    Node 1: Generate prompt embedding via Ollama API and query pgvector
    for the most relevant historical interactions to serve as RAG context.
    """
    start_time = time.time()
    prompt = state["prompt"]

    # Request vector embedding from Ollama
    emb_resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": "llama3", "prompt": prompt},
        timeout=30
    )
    emb_resp.raise_for_status()
    embedding = emb_resp.json().get("embedding", [])
    state["embedding"] = embedding

    # Perform vector similarity search using L2 distance (<=> operator) in pgvector
    context_str = ""
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT prompt, response, 1 - (embedding <=> %s::vector) AS similarity
                FROM embeddings_store
                ORDER BY embedding <=> %s::vector
                LIMIT 2;
            """, (embedding, embedding))
            matches = cur.fetchall()
            if matches:
                context_str = "\n".join([f"Prior Prompt: {m[0]}\nPrior Answer: {m[1]}" for m in matches])
        conn.close()
    except Exception as e:
        print(f"Vector search warning: {e}")

    state["context"] = context_str
    GRAPH_NODE_LATENCY.labels(node="embed_and_retrieve").observe(time.time() - start_time)
    return state

def generate_node(state: AgentState) -> AgentState:
    """
    Node 2: Construct augmented prompt with RAG context and invoke Ollama LLM.
    """
    start_time = time.time()
    prompt = state["prompt"]
    context = state.get("context", "")

    # Inject context if prior matches were retrieved
    full_prompt = f"Context from past interactions:\n{context}\n\nUser Question: {prompt}" if context else prompt

    response = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": "llama3", "prompt": full_prompt, "stream": False},
        timeout=60
    )
    response.raise_for_status()
    state["ai_response"] = response.json().get("response", "")
    GRAPH_NODE_LATENCY.labels(node="generate").observe(time.time() - start_time)
    return state

def persist_node(state: AgentState) -> AgentState:
    """
    Node 3: Save generated response text file to LocalStack S3 and insert
    prompt, response, and embedding into PostgreSQL pgvector table.
    """
    start_time = time.time()
    ai_response = state["ai_response"]
    prompt = state["prompt"]
    embedding = state.get("embedding", [])

    # 1. Upload generated text response to LocalStack S3
    file_name = f"result-{uuid.uuid4().hex[:8]}.txt"
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=file_name,
        Body=ai_response.encode("utf-8")
    )
    state["saved_file"] = file_name

    # 2. Persist record into PostgreSQL vector database
    try:
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO embeddings_store (id, prompt, response, embedding)
                VALUES (%s, %s, %s, %s::vector);
            """, (str(uuid.uuid4()), prompt, ai_response, embedding))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Failed to persist record to pgvector: {e}")

    GRAPH_NODE_LATENCY.labels(node="persist").observe(time.time() - start_time)
    return state

# ==============================================================================
# LANGGRAPH EXECUTION ENGINE COMPILATION
# ==============================================================================
builder = StateGraph(AgentState)
builder.add_node("embed_and_retrieve", embed_and_retrieve_node)
builder.add_node("generate", generate_node)
builder.add_node("persist", persist_node)

# Flow: START -> embed_and_retrieve -> generate -> persist -> END
builder.set_entry_point("embed_and_retrieve")
builder.add_edge("embed_and_retrieve", "generate")
builder.add_edge("generate", "persist")
builder.add_edge("persist", END)

langgraph_engine = builder.compile()

# ==============================================================================
# FLASK HTTP ENDPOINTS
# ==============================================================================
@app.route("/health", methods=["GET"])
def health():
    """Kubernetes liveness and readiness probe endpoint."""
    return jsonify({"status": "healthy"}), 200

@app.route("/metrics", methods=["GET"])
def metrics():
    """Prometheus metrics scraping endpoint."""
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route("/process", methods=["POST"])
def process():
    """Main REST endpoint receiving JSON payload and triggering the LangGraph agent."""
    data = request.get_json()
    if not data or "prompt" not in data:
        REQUEST_COUNT.labels(status="bad_request").inc()
        return jsonify({"error": "No prompt provided"}), 400

    try:
        initial_state = {
            "prompt": data["prompt"],
            "embedding": [],
            "context": "",
            "ai_response": "",
            "saved_file": ""
        }

        # Invoke the compiled LangGraph workflow
        final_state = langgraph_engine.invoke(initial_state)
        REQUEST_COUNT.labels(status="success").inc()

        return jsonify({
            "status": "success",
            "saved_file": final_state["saved_file"],
            "context_retrieved": bool(final_state["context"]),
            "ai_response": final_state["ai_response"]
        }), 200

    except Exception as e:
        REQUEST_COUNT.labels(status="error").inc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
