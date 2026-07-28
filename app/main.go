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
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/stdout/stdouttrace"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.4.0"
)

var tracer = otel.Tracer("go-backend")

// initTracer initializes OpenTelemetry stdout trace exporter
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
		log.Fatalf("failed to initialize tracer: %v", err)
	}
	defer func() { _ = tp.Shutdown(context.Background()) }()

	agentURL := os.Getenv("AGENT_URL")
	if agentURL == "" {
		agentURL = "http://agent-service:5000"
	}

	r := gin.Default()

	r.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "healthy"})
	})

	r.POST("/prompt", func(c *gin.Context) {
		ctx, span := tracer.Start(c.Request.Context(), "HandlePrompt")
		defer span.End()

		var req PromptRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}

		jsonData, _ := json.Marshal(req)
		agentReq, err := http.NewRequestWithContext(ctx, "POST", fmt.Sprintf("%s/process", agentURL), bytes.NewBuffer(jsonData))
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create agent request"})
			return
		}
		agentReq.Header.Set("Content-Type", "application/json")

		client := &http.Client{}
		resp, err := client.Do(agentReq)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to communicate with agent"})
			return
		}
		defer resp.Body.Close()

		body, _ := io.ReadAll(resp.Body)
		c.Data(resp.StatusCode, "application/json", body)
	})

	log.Println("Starting Go backend on port 8080...")
	_ = r.Run(":8080")
}
