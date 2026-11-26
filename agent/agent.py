"""
AI Agent Microservice utilizing Flask, LangGraph, pgvector, and Prometheus.

This service implements a stateful RAG (Retrieval-Augmented Generation) workflow:
1. Receives prompt via HTTP POST.
2. Embeds the prompt and retrieves context from PostgreSQL (pgvector).
3. Invokes Ollama (llama3 model) on the host system.
4. Persists AI text output to LocalStack S3 and saves vector embeddings to PostgreSQL.
5. Exposes Prometheus metrics for scrapers.
"""

from contextlib import contextmanager
import os
import time
from typing import Iterator, List, TypedDict
import uuid

import boto3
from flask import Flask, Response, jsonify, request
from langgraph.graph import END, StateGraph
from pgvector.psycopg2 import register_vector
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
import psycopg2
from psycopg2.extensions import connection as PgConnection
import requests

app: Flask = Flask(__name__)

# Prometheus metrics
REQUEST_COUNT: Counter = Counter(
    "agent_requests_total",
    "Total prompt processing requests handled by the Python AI agent",
    ["status"],
)

GRAPH_NODE_LATENCY: Histogram = Histogram(
    "agent_graph_node_latency_seconds",
    "Latency distribution per LangGraph workflow node in seconds",
    ["node"],
)

# Service URLs targeting host bridge or internal cluster DNS
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
LOCALSTACK_URL: str = os.getenv("LOCALSTACK_URL", "http://host.docker.internal:4566")
BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "ai-agent-storage")
POSTGRES_URL: str = os.getenv(
    "POSTGRES_URL",
    "postgresql://postgres:postgres@postgres-service:5432/aisandbox",
)

s3_client = boto3.client(
    "s3",
    region_name="us-east-1",
    aws_access_key_id="mock_key",
    aws_secret_access_key="mock_secret",
    endpoint_url=LOCALSTACK_URL,
)


@contextmanager
def db_connection(enable_vector: bool = True) -> Iterator[PgConnection]:
    conn = psycopg2.connect(POSTGRES_URL)
    if enable_vector:
        register_vector(conn)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Ensure pgvector extension and embeddings_store table exist, retrying container startup races."""
    retries = 10
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            # Create the extension before register_vector() queries pg_type for 'vector'.
            with db_connection(enable_vector=False) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            with db_connection(enable_vector=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS embeddings_store (
                            id UUID PRIMARY KEY,
                            prompt TEXT NOT NULL,
                            response TEXT NOT NULL,
                            embedding vector(4096),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                        """
                    )
            print("Database and pgvector schema initialized successfully.")
            return
        except Exception as err:
            last_error = err
            print(f"Database init attempt {attempt + 1}/{retries} failed: {err}. Retrying in 2s...")
            time.sleep(2)

    raise RuntimeError(f"Failed to initialize PostgreSQL pgvector schema: {last_error}")


init_db()


class AgentState(TypedDict):
    prompt: str
    embedding: List[float]
    context: str
    ai_response: str
    saved_file: str


def embed_and_retrieve_node(state: AgentState) -> AgentState:
    """Generate prompt embedding via Ollama and query pgvector for RAG context."""
    start_time = time.time()
    prompt = state["prompt"]

    emb_resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": "llama3", "prompt": prompt},
        timeout=120,
    )
    emb_resp.raise_for_status()
    embedding: List[float] = emb_resp.json().get("embedding", [])
    if not embedding:
        raise ValueError("Ollama returned an empty embedding vector")
    state["embedding"] = embedding

    # Vector similarity search using pgvector cosine distance (<=> operator)
    context_str = ""
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prompt, response, 1 - (embedding <=> %s::vector) AS similarity
                FROM embeddings_store
                ORDER BY embedding <=> %s::vector
                LIMIT 2;
                """,
                (embedding, embedding),
            )
            matches = cur.fetchall()
            if matches:
                context_str = "\n".join(
                    f"Prior Prompt: {row[0]}\nPrior Answer: {row[1]}" for row in matches
                )

    state["context"] = context_str
    GRAPH_NODE_LATENCY.labels(node="embed_and_retrieve").observe(time.time() - start_time)
    return state


def generate_node(state: AgentState) -> AgentState:
    """Augment prompt with retrieved context and invoke Ollama LLM."""
    start_time = time.time()
    prompt = state["prompt"]
    context = state.get("context", "")

    full_prompt = (
        f"Context from past interactions:\n{context}\n\nUser Question: {prompt}"
        if context
        else prompt
    )

    response = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": "llama3", "prompt": full_prompt, "stream": False},
        timeout=180,
    )
    response.raise_for_status()
    state["ai_response"] = response.json().get("response", "")
    GRAPH_NODE_LATENCY.labels(node="generate").observe(time.time() - start_time)
    return state


def persist_node(state: AgentState) -> AgentState:
    """Persist response artifact to LocalStack S3 and store embedding in PostgreSQL."""
    start_time = time.time()
    ai_response = state["ai_response"]
    prompt = state["prompt"]
    embedding = state["embedding"]

    file_name = f"result-{uuid.uuid4().hex[:8]}.txt"
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=file_name,
        Body=ai_response.encode("utf-8"),
    )
    state["saved_file"] = file_name

    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO embeddings_store (id, prompt, response, embedding)
                VALUES (%s, %s, %s, %s::vector);
                """,
                (str(uuid.uuid4()), prompt, ai_response, embedding),
            )

    GRAPH_NODE_LATENCY.labels(node="persist").observe(time.time() - start_time)
    return state


builder = StateGraph(AgentState)
builder.add_node("embed_and_retrieve", embed_and_retrieve_node)
builder.add_node("generate", generate_node)
builder.add_node("persist", persist_node)
builder.set_entry_point("embed_and_retrieve")
builder.add_edge("embed_and_retrieve", "generate")
builder.add_edge("generate", "persist")
builder.add_edge("persist", END)

langgraph_engine = builder.compile()


@app.route("/health", methods=["GET"])
def health() -> tuple[Response, int]:
    """Kubernetes liveness and readiness probe endpoint."""
    return jsonify({"status": "healthy"}), 200


@app.route("/metrics", methods=["GET"])
def metrics() -> Response:
    """Prometheus metrics scraping endpoint."""
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/process", methods=["POST"])
def process() -> tuple[Response, int]:
    """REST endpoint triggering the LangGraph RAG workflow."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "prompt" not in data or not isinstance(data["prompt"], str):
        REQUEST_COUNT.labels(status="bad_request").inc()
        return jsonify({"error": "JSON body with string 'prompt' is required"}), 400

    prompt_text = data["prompt"].strip()
    if not prompt_text:
        REQUEST_COUNT.labels(status="bad_request").inc()
        return jsonify({"error": "Prompt must be non-empty"}), 400

    try:
        initial_state: AgentState = {
            "prompt": prompt_text,
            "embedding": [],
            "context": "",
            "ai_response": "",
            "saved_file": "",
        }
        final_state = langgraph_engine.invoke(initial_state)
        REQUEST_COUNT.labels(status="success").inc()

        return (
            jsonify(
                {
                    "status": "success",
                    "saved_file": final_state["saved_file"],
                    "context_retrieved": bool(final_state["context"]),
                    "ai_response": final_state["ai_response"],
                }
            ),
            200,
        )
    except Exception as err:
        REQUEST_COUNT.labels(status="error").inc()
        return jsonify({"error": str(err)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
