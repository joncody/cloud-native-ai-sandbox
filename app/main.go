// Package main implements a Go API Gateway microservice using Gin,
// OpenTelemetry stdout trace exporting, and Prometheus metrics.
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
	"time"

	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/stdout/stdouttrace"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.4.0"
)

var tracer = otel.Tracer("go-backend")

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

func init() {
	prometheus.MustRegister(httpRequestsTotal)
	prometheus.MustRegister(httpRequestDuration)
}

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

type PromptRequest struct {
	Prompt string `json:"prompt" binding:"required"`
}

func main() {
	tp, err := initTracer()
	if err != nil {
		log.Fatalf("failed to initialize OpenTelemetry tracer: %v", err)
	}
	defer func() { _ = tp.Shutdown(context.Background()) }()

	agentURL := os.Getenv("AGENT_URL")
	if agentURL == "" {
		agentURL = "http://agent-service:5000"
	}

	httpClient := &http.Client{
		Timeout: 300 * time.Second,
	}

	r := gin.Default()

	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "healthy"})
	})

	r.GET("/metrics", gin.WrapH(promhttp.Handler()))

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

		jsonData, err := json.Marshal(req)
		if err != nil {
			httpRequestsTotal.WithLabelValues("/prompt", "500").Inc()
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to encode request"})
			return
		}

		agentReq, err := http.NewRequestWithContext(
			ctx,
			http.MethodPost,
			fmt.Sprintf("%s/process", agentURL),
			bytes.NewBuffer(jsonData),
		)
		if err != nil {
			httpRequestsTotal.WithLabelValues("/prompt", "500").Inc()
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create agent request"})
			return
		}
		agentReq.Header.Set("Content-Type", "application/json")

		resp, err := httpClient.Do(agentReq)
		if err != nil {
			httpRequestsTotal.WithLabelValues("/prompt", "502").Inc()
			c.JSON(http.StatusBadGateway, gin.H{"error": "failed to communicate with agent"})
			return
		}
		defer func() { _ = resp.Body.Close() }()

		body, err := io.ReadAll(resp.Body)
		if err != nil {
			httpRequestsTotal.WithLabelValues("/prompt", "502").Inc()
			c.JSON(http.StatusBadGateway, gin.H{"error": "failed to read agent response"})
			return
		}

		httpRequestsTotal.WithLabelValues("/prompt", fmt.Sprintf("%d", resp.StatusCode)).Inc()
		c.Data(resp.StatusCode, "application/json", body)
	})

	log.Println("Starting Go API Gateway on port 8080...")
	if err := r.Run(":8080"); err != nil {
		log.Fatalf("gateway server failed: %v", err)
	}
}
