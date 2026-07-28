# Kubernetes namespace resource for sandbox microservices
resource "kubernetes_namespace" "sandbox" {
  metadata {
    name = "ai-sandbox"
  }
}

# LocalStack S3 bucket resource for AI agent response storage
resource "aws_s3_bucket" "ai_storage" {
  bucket        = "ai-agent-storage"
  force_destroy = true
}

# Output declaring the created S3 bucket ID
output "s3_bucket_name" {
  value       = aws_s3_bucket.ai_storage.id
  description = "The name of the simulated S3 bucket"
}
