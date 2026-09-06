package geogateway

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// GeometryValidator is the seam for geometry validation engines. The default
// implementation is the deterministic InternalValidator; a Rust validator CLI
// implementation can be injected in production.
type GeometryValidator interface {
	Name() string
	ValidateGeoJSON(geom map[string]any) (errors []string, area float64, bbox *BBox, geomType string)
}

// JobDispatcher is the seam for enqueueing jobs. The default is the
// deterministic in-memory LocalDispatcher; production injects a dispatcher
// that forwards to the Python orchestration service.
type JobDispatcher interface {
	Enqueue(ctx context.Context, job Job) error
}

// JobStore is the seam for retrieving jobs by ID.
type JobStore interface {
	Get(ctx context.Context, tenantStateID, jobID string) (Job, bool)
	Put(ctx context.Context, job Job)
}

// PythonServiceClient is the seam for the Python mod-geospatial service. The
// gateway validates and redacts GeoLibre project requests; production
// deployments inject an HTTP client, while local/test mode uses
// LocalPythonClient, which only acknowledges acceptance.
type PythonServiceClient interface {
	AcceptGeoLibreProject(ctx context.Context, req GeoLibreProjectResponse) error
}

// RustValidatorCLI is the seam for invoking the external Rust geometry
// validator binary. Local/test mode leaves this nil and uses the internal
// validator. Production must fail closed when the binary is unavailable.
type RustValidatorCLI interface {
	ValidateGeoJSON(ctx context.Context, geom map[string]any) (GeometryValidationResponse, error)
}

// ErrNotFound is returned when a job is absent from the store.
var ErrNotFound = errors.New("job not found")

// InMemoryJobStore is a deterministic, tenant-scoped in-memory job store.
type InMemoryJobStore struct {
	mu   sync.RWMutex
	jobs map[string]Job // key: tenant + "/" + jobID
}

// NewInMemoryJobStore constructs an empty store.
func NewInMemoryJobStore() *InMemoryJobStore {
	return &InMemoryJobStore{jobs: make(map[string]Job)}
}

func storeKey(tenant, jobID string) string { return tenant + "/" + jobID }

// Get retrieves a job scoped to the tenant.
func (s *InMemoryJobStore) Get(_ context.Context, tenantStateID, jobID string) (Job, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	j, ok := s.jobs[storeKey(tenantStateID, jobID)]
	return j, ok
}

// Put inserts or replaces a job, keyed by its tenant.
func (s *InMemoryJobStore) Put(_ context.Context, job Job) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.jobs[storeKey(job.TenantStateID, job.JobID)] = job
}

// LocalDispatcher enqueues jobs into the in-memory store deterministically.
type LocalDispatcher struct {
	Store JobStore
	Now   func() time.Time // injectable clock; defaults to time.Now
}

// Enqueue stores the job in QUEUED status.
func (d *LocalDispatcher) Enqueue(ctx context.Context, job Job) error {
	if d.Store == nil {
		return errors.New("local dispatcher requires a store")
	}
	job.Status = JobStatusQueued
	if job.CreatedAt.IsZero() {
		now := time.Now
		if d.Now != nil {
			now = d.Now
		}
		job.CreatedAt = now().UTC()
	}
	d.Store.Put(ctx, job)
	return nil
}

// LocalPythonClient is the deterministic local PythonServiceClient seam. It
// performs no network calls and simply accepts well-formed requests.
type LocalPythonClient struct{}

// AcceptGeoLibreProject accepts the redacted request locally.
func (LocalPythonClient) AcceptGeoLibreProject(_ context.Context, req GeoLibreProjectResponse) error {
	if req.RequestHash == "" {
		return errors.New("redacted request hash is required")
	}
	return nil
}

// Service wires the seams and implements the domain operations.
type Service struct {
	Validator    GeometryValidator
	Dispatcher   JobDispatcher
	Store        JobStore
	PythonClient PythonServiceClient
	RustCLI      RustValidatorCLI // optional; when nil the internal validator is used
	IDGenerator  func() string    // injectable deterministic ID generator
}

// NewLocalService builds a Service with deterministic local defaults.
func NewLocalService(idGen func() string) *Service {
	store := NewInMemoryJobStore()
	if idGen == nil {
		var counter int
		var mu sync.Mutex
		idGen = func() string {
			mu.Lock()
			defer mu.Unlock()
			counter++
			return fmt.Sprintf("job-%08d", counter)
		}
	}
	return &Service{
		Validator:    InternalValidator{},
		Dispatcher:   &LocalDispatcher{Store: store},
		Store:        store,
		PythonClient: LocalPythonClient{},
		IDGenerator:  idGen,
	}
}

// ValidateGeometry runs the configured validator seam.
func (s *Service) ValidateGeometry(ctx context.Context, tenant string, req GeometryValidationRequest) GeometryValidationResponse {
	if req.CRS != "" && req.CRS != "EPSG:4326" && req.CRS != "urn:ogc:def:crs:OGC::CRS84" {
		return GeometryValidationResponse{
			Valid:     false,
			Errors:    []string{fmt.Sprintf("unsupported CRS %q: only EPSG:4326 lon/lat is accepted", req.CRS)},
			Validator: s.Validator.Name(),
		}
	}
	// If a Rust CLI seam is configured, prefer it and fail closed on error.
	if s.RustCLI != nil {
		resp, err := s.RustCLI.ValidateGeoJSON(ctx, req.Geometry)
		if err != nil {
			return GeometryValidationResponse{
				Valid:     false,
				Errors:    []string{fmt.Sprintf("rust validator unavailable: %s", err.Error())},
				Validator: "rust-cli",
			}
		}
		return resp
	}
	errs, area, bbox, geomType := s.Validator.ValidateGeoJSON(req.Geometry)
	return GeometryValidationResponse{
		Valid:     len(errs) == 0,
		Errors:    errs,
		Area:      area,
		BBox:      bbox,
		Geometry:  geomType,
		Validator: s.Validator.Name(),
	}
}

// CreateJob validates and enqueues a processing job.
func (s *Service) CreateJob(ctx context.Context, tenant string, req CreateJobRequest) (Job, []string, error) {
	var errs []string
	if !req.JobType.valid() {
		errs = append(errs, fmt.Sprintf("unsupported job_type %q", req.JobType))
	}
	if len(errs) > 0 {
		return Job{}, errs, ErrNotFound
	}
	job := Job{
		JobID:         s.IDGenerator(),
		TenantStateID: tenant,
		JobType:       req.JobType,
		Status:        JobStatusQueued,
		Parameters:    req.Parameters,
	}
	if err := s.Dispatcher.Enqueue(ctx, job); err != nil {
		return Job{}, nil, fmt.Errorf("dispatch failed: %w", err)
	}
	return job, nil, nil
}

// GetJob retrieves a job scoped to the tenant.
func (s *Service) GetJob(ctx context.Context, tenant, jobID string) (Job, bool) {
	return s.Store.Get(ctx, tenant, jobID)
}

// BuildGeoLibreProject validates and redacts a GeoLibre project request and
// passes it through the Python client seam. No raw geometry or PII is ever
// included — only dataset/job references and hashes.
func (s *Service) BuildGeoLibreProject(ctx context.Context, tenant string, req GeoLibreProjectRequest) (GeoLibreProjectResponse, []string, error) {
	var errs []string
	if req.Name == "" {
		errs = append(errs, "name is required")
	}
	if len(req.DatasetIDs) == 0 {
		errs = append(errs, "at least one dataset_id is required")
	}
	for i, id := range req.DatasetIDs {
		if id == "" {
			errs = append(errs, fmt.Sprintf("dataset_ids[%d] must be non-empty", i))
		}
	}
	if len(errs) > 0 {
		return GeoLibreProjectResponse{}, errs, ErrNotFound
	}
	parts := []string{tenant, req.Name, req.SourceJobID}
	parts = append(parts, req.DatasetIDs...)
	resp := GeoLibreProjectResponse{
		ProjectID:      "geolibre-" + hashRequest(parts...)[:16],
		TenantStateID:  tenant,
		Name:           req.Name,
		DatasetIDs:     req.DatasetIDs,
		SourceJobID:    req.SourceJobID,
		RedactionLevel: "hash-only",
		RequestHash:    hashRequest(parts...),
		Accepted:       true,
	}
	if err := s.PythonClient.AcceptGeoLibreProject(ctx, resp); err != nil {
		return GeoLibreProjectResponse{}, nil, fmt.Errorf("python service rejected project request: %w", err)
	}
	return resp, nil, nil
}
