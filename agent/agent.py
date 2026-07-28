import os
import uuid
import boto3
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Fetch service URLs from environment variables with defaults targeting host gateway IP
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://172.19.0.1:11434")
LOCALSTACK_URL = os.getenv("LOCALSTACK_URL", "http://172.19.0.1:4566")
BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "ai-agent-storage")

# Initialize boto3 S3 client targeting LocalStack endpoint
s3_client = boto3.client(
    "s3",
    region_name="us-east-1",
    aws_access_key_id="mock_key",
    aws_secret_access_key="mock_secret",
    endpoint_url=LOCALSTACK_URL,
)

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint for Kubernetes probes."""
    return jsonify({"status": "healthy"}), 200

@app.route("/process", methods=["POST"])
def process():
    """Receives prompt request, invokes Ollama API, and saves response to LocalStack S3."""
    data = request.get_json()
    if not data or "prompt" not in data:
        return jsonify({"error": "No prompt provided"}), 400

    prompt = data["prompt"]

    try:
        # Send generation request to Ollama on host
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": "llama3", "prompt": prompt, "stream": False},
            timeout=30
        )
        response.raise_for_status()
        ai_response = response.json().get("response", "")

        # Generate unique filename and store completion text in LocalStack S3
        file_name = f"result-{uuid.uuid4().hex[:8]}.txt"
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=file_name,
            Body=ai_response.encode("utf-8")
        )

        return jsonify({
            "status": "success",
            "saved_file": file_name,
            "ai_response": ai_response
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
