package geogateway

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

// Environment configuration for the Temporal job dispatcher seam.
const (
	// EnvDispatcher selects the dispatcher backend: "" or "local" uses the
	// deterministic LocalDispatcher; "temporal" selects TemporalDispatcher.
	EnvDispatcher = "GEO_GATEWAY_DISPATCHER"
	// EnvTemporalAddress is the Temporal frontend address (host:port).
	EnvTemporalAddress = "GEO_GATEWAY_TEMPORAL_ADDRESS"
	// EnvTemporalNamespace is the Temporal namespace (default "default").
	EnvTemporalNamespace = "GEO_GATEWAY_TEMPORAL_NAMESPACE"
)

// TemporalWorkflowStarter is the seam over the Temporal SDK client
// (go.temporal.io/sdk/client). The SDK is only compiled into the production
// worker image; tests inject a fake. “StartWorkflow“ must return the
// workflow ID of the started GeospatialJobWorkflow run.
type TemporalWorkflowStarter interface {
	StartWorkflow(ctx context.Context, taskQueue, workflowID string, job Job) (string, error)
}

// TemporalDispatcher enqueues geospatial jobs as durable Temporal workflows
// on the per-tenant task queue “geospatial-jobs-<state_id>“ (mirroring the
// Python services/mod-geospatial temporal adapter).
type TemporalDispatcher struct {
	Starter   TemporalWorkflowStarter
	Namespace string
	Now       func() time.Time // injectable clock; defaults to time.Now
}

// TaskQueueForState returns the per-tenant Temporal task queue.
func TaskQueueForState(stateID string) string {
	state := strings.ToLower(strings.TrimSpace(stateID))
	var b strings.Builder
	for _, r := range state {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' {
			b.WriteRune(r)
		} else {
			b.WriteRune('-')
		}
	}
	return "geospatial-jobs-" + b.String()
}

// NewTemporalDispatcher builds the Temporal dispatcher from configuration.
// It fails closed when no Temporal frontend address is configured or no
// workflow starter (SDK client) has been injected — production must never
// silently drop jobs.
func NewTemporalDispatcher(address, namespace string, starter TemporalWorkflowStarter) (*TemporalDispatcher, error) {
	if strings.TrimSpace(address) == "" {
		return nil, fmt.Errorf("%s is not set; Temporal dispatcher fails closed", EnvTemporalAddress)
	}
	if starter == nil {
		return nil, errors.New("temporal workflow starter (go.temporal.io/sdk client) is not wired; dispatcher fails closed")
	}
	if strings.TrimSpace(namespace) == "" {
		namespace = "default"
	}
	return &TemporalDispatcher{Starter: starter, Namespace: namespace}, nil
}

// NewDispatcherFromEnv selects the job dispatcher from GEO_GATEWAY_DISPATCHER.
// "temporal" requires GEO_GATEWAY_TEMPORAL_ADDRESS and an injected starter;
// anything else returns the deterministic LocalDispatcher.
func NewDispatcherFromEnv(store JobStore, starter TemporalWorkflowStarter) (JobDispatcher, error) {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(EnvDispatcher))) {
	case "", "local":
		return &LocalDispatcher{Store: store}, nil
	case "temporal":
		return NewTemporalDispatcher(os.Getenv(EnvTemporalAddress), os.Getenv(EnvTemporalNamespace), starter)
	default:
		return nil, fmt.Errorf("unsupported %s value %q", EnvDispatcher, os.Getenv(EnvDispatcher))
	}
}

// Enqueue starts a GeospatialJobWorkflow for the job on the per-tenant task
// queue and records the QUEUED job in the store when one is attached.
func (d *TemporalDispatcher) Enqueue(ctx context.Context, job Job) error {
	if d == nil || d.Starter == nil {
		return errors.New("temporal dispatcher is not configured")
	}
	job.Status = JobStatusQueued
	if job.CreatedAt.IsZero() {
		now := time.Now
		if d.Now != nil {
			now = d.Now
		}
		job.CreatedAt = now().UTC()
	}
	workflowID := "geospatial-job-" + job.JobID
	if _, err := d.Starter.StartWorkflow(ctx, TaskQueueForState(job.TenantStateID), workflowID, job); err != nil {
		return fmt.Errorf("temporal workflow start failed: %w", err)
	}
	return nil
}
