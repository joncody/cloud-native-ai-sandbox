# Multi-stage Docker build for Go API gateway using Golang 1.26
FROM golang:1.26-alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY main.go .
RUN CGO_ENABLED=0 GOOS=linux go build -o backend main.go

FROM alpine:latest
WORKDIR /app
COPY --from=builder /app/backend .

EXPOSE 8080

CMD ["./backend"]
