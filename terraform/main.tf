# OpenTofu / Terraform main configuration defining Kubernetes namespace,
# LocalStack S3 bucket, and database secret credentials.

# Kubernetes namespace resource for sandbox microservices
resource "kubernetes_namespace" "sandbox" {
  metadata {
    name = "ai-sandbox"
  }
}

# LocalStack S3 bucket resource for AI agent response file storage
resource "aws_s3_bucket" "ai_storage" {
  bucket        = "ai-agent-storage"
  force_destroy = true
}

# Kubernetes secret storing PostgreSQL connection details
resource "kubernetes_secret" "postgres_credentials" {
  metadata {
    name      = "postgres-credentials"
    namespace = kubernetes_namespace.sandbox.metadata[0].name
  }

  data = {
    username = "postgres"
    password = "postgres"
    database = "aisandbox"
    url      = "postgresql://postgres:postgres@postgres-service:5432/aisandbox"
  }
}

# Output declaring the created S3 bucket ID
output "s3_bucket_name" {
  value       = aws_s3_bucket.ai_storage.id
  description = "The name of the simulated S3 bucket"
}
