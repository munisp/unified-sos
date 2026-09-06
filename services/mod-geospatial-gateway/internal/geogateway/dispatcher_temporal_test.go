package geogateway

import (
	"context"
	"errors"
	"testing"
)

type fakeStarter struct {
	calls      []startCall
	failWith   error
	workflowID string
}

type startCall struct {
	taskQueue  string
	workflowID string
	job        Job
}

func (f *fakeStarter) StartWorkflow(_ context.Context, taskQueue, workflowID string, job Job) (string, error) {
	if f.failWith != nil {
		return "", f.failWith
	}
	f.calls = append(f.calls, startCall{taskQueue: taskQueue, workflowID: workflowID, job: job})
	if f.workflowID != "" {
		return f.workflowID, nil
	}
	return workflowID, nil
}

func TestTaskQueueForState(t *testing.T) {
	cases := map[string]string{
		"osun":        "geospatial-jobs-osun",
		"Lagos":       "geospatial-jobs-lagos",
		"nasarawa ":   "geospatial-jobs-nasarawa",
		"Ogun_State":  "geospatial-jobs-ogun-state",
		"benue.north": "geospatial-jobs-benue-north",
	}
	for in, want := range cases {
		if got := TaskQueueForState(in); got != want {
			t.Errorf("TaskQueueForState(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestNewTemporalDispatcherFailClosed(t *testing.T) {
	if _, err := NewTemporalDispatcher("", "default", &fakeStarter{}); err == nil {
		t.Fatal("missing Temporal address must fail closed")
	}
	if _, err := NewTemporalDispatcher("localhost:7233", "default", nil); err == nil {
		t.Fatal("nil workflow starter must fail closed")
	}
}

func TestNewTemporalDispatcherDefaultsNamespace(t *testing.T) {
	d, err := NewTemporalDispatcher("localhost:7233", "", &fakeStarter{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if d.Namespace != "default" {
		t.Fatalf("namespace = %q, want default", d.Namespace)
	}
}

func TestTemporalDispatcherEnqueueStartsWorkflow(t *testing.T) {
	starter := &fakeStarter{}
	d, err := NewTemporalDispatcher("localhost:7233", "sos", starter)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	job := Job{JobID: "job-00000001", TenantStateID: "osun", JobType: JobTypeH3Aggregation}
	if err := d.Enqueue(context.Background(), job); err != nil {
		t.Fatalf("Enqueue: %v", err)
	}
	if len(starter.calls) != 1 {
		t.Fatalf("expected 1 workflow start, got %d", len(starter.calls))
	}
	call := starter.calls[0]
	if call.taskQueue != "geospatial-jobs-osun" {
		t.Fatalf("task queue = %q, want geospatial-jobs-osun", call.taskQueue)
	}
	if call.workflowID != "geospatial-job-job-00000001" {
		t.Fatalf("workflow ID = %q", call.workflowID)
	}
	if call.job.Status != JobStatusQueued {
		t.Fatalf("job status = %q, want QUEUED", call.job.Status)
	}
	if call.job.CreatedAt.IsZero() {
		t.Fatal("CreatedAt must be stamped")
	}
}

func TestTemporalDispatcherEnqueuePropagatesFailure(t *testing.T) {
	starter := &fakeStarter{failWith: errors.New("temporal unavailable")}
	d, err := NewTemporalDispatcher("localhost:7233", "sos", starter)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if err := d.Enqueue(context.Background(), Job{JobID: "j1", TenantStateID: "osun", JobType: JobTypeH3Aggregation}); err == nil {
		t.Fatal("workflow start failure must propagate")
	}
}

func TestNewDispatcherFromEnvDefaultsToLocal(t *testing.T) {
	t.Setenv(EnvDispatcher, "")
	d, err := NewDispatcherFromEnv(NewInMemoryJobStore(), nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, ok := d.(*LocalDispatcher); !ok {
		t.Fatalf("default dispatcher = %T, want *LocalDispatcher", d)
	}
}

func TestNewDispatcherFromEnvTemporalFailClosed(t *testing.T) {
	t.Setenv(EnvDispatcher, "temporal")
	t.Setenv(EnvTemporalAddress, "")
	if _, err := NewDispatcherFromEnv(NewInMemoryJobStore(), nil); err == nil {
		t.Fatal("temporal mode without address must fail closed")
	}
	t.Setenv(EnvTemporalAddress, "localhost:7233")
	if _, err := NewDispatcherFromEnv(NewInMemoryJobStore(), nil); err == nil {
		t.Fatal("temporal mode without SDK starter must fail closed")
	}
	d, err := NewDispatcherFromEnv(NewInMemoryJobStore(), &fakeStarter{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, ok := d.(*TemporalDispatcher); !ok {
		t.Fatalf("dispatcher = %T, want *TemporalDispatcher", d)
	}
}

func TestNewDispatcherFromEnvRejectsUnknown(t *testing.T) {
	t.Setenv(EnvDispatcher, "kafka")
	if _, err := NewDispatcherFromEnv(NewInMemoryJobStore(), nil); err == nil {
		t.Fatal("unknown dispatcher value must fail closed")
	}
}
