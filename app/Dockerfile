# Multi-stage Docker build for Go API Gateway using Golang 1.26 toolchain
FROM golang:1.26-alpine AS builder

WORKDIR /app

# Copy module definition files first to leverage Docker layer caching
COPY go.mod go.sum* ./
RUN go mod download

# Copy source code and build static binary
COPY main.go .
RUN CGO_ENABLED=0 GOOS=linux go build -o backend main.go

# Minimal runtime stage using Alpine Linux
FROM alpine:latest
WORKDIR /app

# Copy compiled binary from builder stage
COPY --from=builder /app/backend .

# Expose API Gateway port
EXPOSE 8080

# Execute API Gateway binary
CMD ["./backend"]
