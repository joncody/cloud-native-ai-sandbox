// Package main implements a high-performance Go API Gateway microservice using Gin,
// OpenTelemetry distributed tracing, and Prometheus metric exporters.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/stdout/stdouttrace"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.4.0"
)

// Global OpenTelemetry tracer instance for creating trace spans
var tracer = otel.Tracer("go-backend")

// Global Prometheus metrics declarations
var (
	httpRequestsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "http_requests_total",
			Help: "Total number of HTTP requests processed by Go API Gateway",
		},
		[]string{"path", "status"},
	)
	httpRequestDuration = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "http_request_duration_seconds",
			Help:    "Latency histogram for HTTP requests processed by Go API Gateway",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"path"},
	)
)

// init registers Prometheus metrics with the global registry
func init() {
	prometheus.MustRegister(httpRequestsTotal)
	prometheus.MustRegister(httpRequestDuration)
}

// initTracer initializes an OpenTelemetry TracerProvider configured to print spans to stdout
func initTracer() (*sdktrace.TracerProvider, error) {
	exporter, err := stdouttrace.New(stdouttrace.WithPrettyPrint())
	if err != nil {
		return nil, err
	}
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(resource.NewWithAttributes(
			semconv.SchemaURL,
			semconv.ServiceNameKey.String("go-backend-service"),
		)),
	)
	otel.SetTracerProvider(tp)
	return tp, nil
}

// PromptRequest represents the expected JSON payload for prompt generation
type PromptRequest struct {
	Prompt string `json:"prompt" binding:"required"`
}

func main() {
	// Initialize OpenTelemetry tracer
	tp, err := initTracer()
	if err != nil {
		log.Fatalf("failed to initialize OpenTelemetry tracer: %v", err)
	}
	defer func() { _ = tp.Shutdown(context.Background()) }()

	// Resolve target Python Agent service URL from environment variable or fallback to K8s DNS
	agentURL := os.Getenv("AGENT_URL")
	if agentURL == "" {
		agentURL = "http://agent-service:5000"
	}

	// Initialize Gin router
	r := gin.Default()

	// Health check endpoint for Kubernetes liveness/readiness probes
	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "healthy"})
	})

	// Prometheus metrics scraping endpoint
	r.GET("/metrics", gin.WrapH(promhttp.Handler()))

	// Main REST proxy endpoint accepting prompt requests and forwarding to Python agent
	r.POST("/prompt", func(c *gin.Context) {
		timer := prometheus.NewTimer(httpRequestDuration.WithLabelValues("/prompt"))
		defer timer.ObserveDuration()

		ctx, span := tracer.Start(c.Request.Context(), "HandlePrompt")
		defer span.End()

		var req PromptRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			httpRequestsTotal.WithLabelValues("/prompt", "400").Inc()
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}

		// Marshal JSON and construct downstream HTTP request
		jsonData, _ := json.Marshal(req)
		agentReq, err := http.NewRequestWithContext(ctx, "POST", fmt.Sprintf("%s/process", agentURL), bytes.NewBuffer(jsonData))
		if err != nil {
			httpRequestsTotal.WithLabelValues("/prompt", "500").Inc()
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create agent request"})
			return
		}
		agentReq.Header.Set("Content-Type", "application/json")

		// Send request to Python LangGraph agent
		client := &http.Client{}
		resp, err := client.Do(agentReq)
		if err != nil {
			httpRequestsTotal.WithLabelValues("/prompt", "500").Inc()
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to communicate with agent"})
			return
		}
		defer resp.Body.Close()

		// Read and forward response body directly back to client
		body, _ := io.ReadAll(resp.Body)
		httpRequestsTotal.WithLabelValues("/prompt", fmt.Sprintf("%d", resp.StatusCode)).Inc()
		c.Data(resp.StatusCode, "application/json", body)
	})

	log.Println("Starting Go API Gateway on port 8080...")
	_ = r.Run(":8080")
}
