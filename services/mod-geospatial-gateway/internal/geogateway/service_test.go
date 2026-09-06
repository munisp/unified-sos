package geogateway_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/unified-sos/mod-geospatial-gateway/internal/geogateway"
)

func validPolygon() map[string]any {
	return map[string]any{
		"type": "Polygon",
		"coordinates": []any{
			[]any{
				[]any{3.0, 6.0}, []any{3.1, 6.0}, []any{3.1, 6.1}, []any{3.0, 6.1}, []any{3.0, 6.0},
			},
		},
	}
}

func TestHealthz(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	geogateway.NewHandler(svc).Routes().ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	var body map[string]string
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body["status"] != "ok" {
		t.Fatalf("unexpected body: %v", body)
	}
}

func TestTenantAllowlist(t *testing.T) {
	for _, tenant := range []string{"lagos", "ogun", "osun", "benue", "nasarawa", "taraba"} {
		if !geogateway.TenantAllowed(tenant) {
			t.Errorf("expected tenant %q allowed", tenant)
		}
	}
	for _, tenant := range []string{"kano", "abuja", "", "LAGOS ", "lagos'\""} {
		if tenant == "LAGOS " {
			// trimmed+lowercased, so allowed
			if !geogateway.TenantAllowed(tenant) {
				t.Errorf("expected %q allowed after normalization", tenant)
			}
			continue
		}
		if geogateway.TenantAllowed(tenant) {
			t.Errorf("expected tenant %q rejected", tenant)
		}
	}
	if got := len(geogateway.AllowedTenants()); got != 6 {
		t.Fatalf("expected 6 allowed tenants, got %d", got)
	}
}

func TestValidateGeometryValidPolygon(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	resp := svc.ValidateGeometry(context.Background(), "lagos", geogateway.GeometryValidationRequest{Geometry: validPolygon()})
	if !resp.Valid {
		t.Fatalf("expected valid, errors: %v", resp.Errors)
	}
	if resp.Validator != "internal" {
		t.Fatalf("expected internal validator, got %q", resp.Validator)
	}
	wantArea := 0.1 * 0.1
	if diff := resp.Area - wantArea; diff < -1e-9 || diff > 1e-9 {
		t.Fatalf("expected area ~%f, got %f", wantArea, resp.Area)
	}
	if resp.BBox == nil || *resp.BBox != (geogateway.BBox{3.0, 6.0, 3.1, 6.1}) {
		t.Fatalf("unexpected bbox: %v", resp.BBox)
	}
}

func TestValidateGeometryInvalid(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	cases := []struct {
		name  string
		geom  map[string]any
		wantE string
	}{
		{"nil geometry", nil, "geometry is required"},
		{"missing coordinates", map[string]any{"type": "Polygon"}, "coordinates is required"},
		{"unsupported type", map[string]any{"type": "Point", "coordinates": []any{3.0, 6.0}}, "unsupported geometry type"},
		{
			"ring not closed",
			map[string]any{"type": "Polygon", "coordinates": []any{[]any{
				[]any{3.0, 6.0}, []any{3.1, 6.0}, []any{3.1, 6.1}, []any{3.0, 6.1},
			}}},
			"ring is not closed",
		},
		{
			"too few points",
			map[string]any{"type": "Polygon", "coordinates": []any{[]any{
				[]any{3.0, 6.0}, []any{3.1, 6.0}, []any{3.0, 6.0},
			}}},
			"at least 4 positions",
		},
		{
			"longitude out of range",
			map[string]any{"type": "Polygon", "coordinates": []any{[]any{
				[]any{3.0, 6.0}, []any{181.0, 6.0}, []any{3.1, 6.1}, []any{3.0, 6.0},
			}}},
			"longitude",
		},
		{
			"latitude out of range",
			map[string]any{"type": "Polygon", "coordinates": []any{[]any{
				[]any{3.0, 6.0}, []any{3.1, 6.0}, []any{3.1, -91.0}, []any{3.0, 6.0},
			}}},
			"latitude",
		},
		{
			"duplicate consecutive vertices",
			map[string]any{"type": "Polygon", "coordinates": []any{[]any{
				[]any{3.0, 6.0}, []any{3.1, 6.0}, []any{3.1, 6.0}, []any{3.1, 6.1}, []any{3.0, 6.0},
			}}},
			"duplicate consecutive vertices",
		},
		{
			"zero area",
			map[string]any{"type": "Polygon", "coordinates": []any{[]any{
				[]any{3.0, 6.0}, []any{3.1, 6.0}, []any{3.2, 6.0}, []any{3.0, 6.0},
			}}},
			"zero area",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp := svc.ValidateGeometry(context.Background(), "ogun", geogateway.GeometryValidationRequest{Geometry: tc.geom})
			if resp.Valid {
				t.Fatalf("expected invalid geometry")
			}
			joined := strings.Join(resp.Errors, "; ")
			if !strings.Contains(joined, tc.wantE) {
				t.Fatalf("expected error containing %q, got %q", tc.wantE, joined)
			}
		})
	}
}

func TestValidateMultiPolygon(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	geom := map[string]any{
		"type": "MultiPolygon",
		"coordinates": []any{
			validPolygon()["coordinates"],
			[]any{[]any{
				[]any{4.0, 7.0}, []any{4.2, 7.0}, []any{4.2, 7.2}, []any{4.0, 7.2}, []any{4.0, 7.0},
			}},
		},
	}
	resp := svc.ValidateGeometry(context.Background(), "benue", geogateway.GeometryValidationRequest{Geometry: geom})
	if !resp.Valid {
		t.Fatalf("expected valid multipolygon, errors: %v", resp.Errors)
	}
	want := 0.01 + 0.04
	if diff := resp.Area - want; diff < -1e-9 || diff > 1e-9 {
		t.Fatalf("expected combined area ~%f, got %f", want, resp.Area)
	}
	if resp.BBox == nil || (*resp.BBox)[0] != 3.0 || (*resp.BBox)[3] != 7.2 {
		t.Fatalf("unexpected multipolygon bbox: %v", resp.BBox)
	}
}

func TestValidateGeometryHandler(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	routes := geogateway.NewHandler(svc).Routes()

	// Allowed tenant, valid polygon -> 200.
	body := `{"geometry": {"type": "Polygon", "coordinates": [[[3,6],[3.1,6],[3.1,6.1],[3,6.1],[3,6]]]}}`
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/v1/states/lagos/geospatial/validate-geometry", strings.NewReader(body))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body %s", rec.Code, rec.Body.String())
	}

	// Invalid polygon -> 422.
	bad := `{"geometry": {"type": "Polygon", "coordinates": [[[3,6],[3.1,6],[3.1,6.1],[3,6.1]]]}}`
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/v1/states/lagos/geospatial/validate-geometry", strings.NewReader(bad))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}

	// Disallowed tenant -> 403.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/v1/states/kano/geospatial/validate-geometry", strings.NewReader(body))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", rec.Code)
	}
}

// recordingDispatcher is a test double for the dispatcher seam.
type recordingDispatcher struct {
	jobs []geogateway.Job
	err  error
}

func (d *recordingDispatcher) Enqueue(_ context.Context, job geogateway.Job) error {
	if d.err != nil {
		return d.err
	}
	d.jobs = append(d.jobs, job)
	return nil
}

func TestCreateJobDispatch(t *testing.T) {
	store := geogateway.NewInMemoryJobStore()
	dispatcher := &recordingDispatcher{}
	svc := &geogateway.Service{
		Validator:    geogateway.InternalValidator{},
		Dispatcher:   dispatcher,
		Store:        store,
		PythonClient: geogateway.LocalPythonClient{},
		IDGenerator:  func() string { return "job-fixed-1" },
	}

	job, valErrs, err := svc.CreateJob(context.Background(), "osun", geogateway.CreateJobRequest{
		JobType:    geogateway.JobTypeUnassessedPropertyJoin,
		Parameters: map[string]string{"dataset": "cadastre"},
	})
	if err != nil || valErrs != nil {
		t.Fatalf("unexpected error: err=%v valErrs=%v", err, valErrs)
	}
	if job.JobID != "job-fixed-1" || job.TenantStateID != "osun" || job.Status != geogateway.JobStatusQueued {
		t.Fatalf("unexpected job: %+v", job)
	}
	if len(dispatcher.jobs) != 1 {
		t.Fatalf("dispatcher not invoked, jobs=%d", len(dispatcher.jobs))
	}

	// Invalid job type rejected without dispatch.
	_, valErrs, err = svc.CreateJob(context.Background(), "osun", geogateway.CreateJobRequest{JobType: "BOGUS"})
	if err == nil || valErrs == nil {
		t.Fatalf("expected validation error for bogus job type")
	}
	if len(dispatcher.jobs) != 1 {
		t.Fatalf("dispatcher should not be invoked for invalid job")
	}

	// Dispatch failure surfaces.
	dispatcher.err = errors.New("backend down")
	_, _, err = svc.CreateJob(context.Background(), "osun", geogateway.CreateJobRequest{JobType: geogateway.JobTypeH3Aggregation})
	if err == nil || !strings.Contains(err.Error(), "dispatch failed") {
		t.Fatalf("expected dispatch failure, got %v", err)
	}
}

func TestJobStoreTenantIsolation(t *testing.T) {
	ctx := context.Background()
	store := geogateway.NewInMemoryJobStore()
	d := &geogateway.LocalDispatcher{
		Store: store,
		Now:   func() time.Time { return time.Date(2026, 9, 6, 0, 0, 0, 0, time.UTC) },
	}
	job := geogateway.Job{JobID: "job-1", TenantStateID: "lagos", JobType: geogateway.JobTypeH3Aggregation}
	if err := d.Enqueue(ctx, job); err != nil {
		t.Fatalf("enqueue: %v", err)
	}

	// Same tenant can read it.
	got, ok := store.Get(ctx, "lagos", "job-1")
	if !ok {
		t.Fatalf("expected job found for lagos")
	}
	if got.Status != geogateway.JobStatusQueued {
		t.Fatalf("expected QUEUED status, got %s", got.Status)
	}
	if !got.CreatedAt.Equal(time.Date(2026, 9, 6, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("expected deterministic clock, got %v", got.CreatedAt)
	}

	// Other tenant cannot read it (tenant isolation).
	if _, ok := store.Get(ctx, "ogun", "job-1"); ok {
		t.Fatalf("tenant isolation violated: ogun read lagos job")
	}
}

func TestJobHandlers(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	routes := geogateway.NewHandler(svc).Routes()

	// Create job.
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/v1/states/taraba/geospatial/jobs",
		strings.NewReader(`{"job_type": "GEOLIBRE_PROJECT_BUILD"}`))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d body %s", rec.Code, rec.Body.String())
	}
	var job geogateway.Job
	if err := json.Unmarshal(rec.Body.Bytes(), &job); err != nil {
		t.Fatalf("decode job: %v", err)
	}
	if job.JobID == "" || job.TenantStateID != "taraba" || job.Status != geogateway.JobStatusQueued {
		t.Fatalf("unexpected job response: %+v", job)
	}

	// Retrieve same tenant -> 200.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodGet, "/v1/states/taraba/geospatial/jobs/"+job.JobID, nil)
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}

	// Retrieve from another tenant -> 404 (isolation).
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodGet, "/v1/states/lagos/geospatial/jobs/"+job.JobID, nil)
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for cross-tenant access, got %d", rec.Code)
	}

	// Bad job type -> 422.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/v1/states/taraba/geospatial/jobs",
		strings.NewReader(`{"job_type": "NOPE"}`))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}

	// Malformed JSON -> 400.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/v1/states/taraba/geospatial/jobs",
		strings.NewReader(`{"job_type":`))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rec.Code)
	}
}

func TestGeoLibreProject(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	ctx := context.Background()

	// Valid request: redacted, hash-only response.
	resp, valErrs, err := svc.BuildGeoLibreProject(ctx, "nasarawa", geogateway.GeoLibreProjectRequest{
		Name:       "Nasarawa cadastre overview",
		DatasetIDs: []string{"ds-1", "ds-2"},
	})
	if err != nil || valErrs != nil {
		t.Fatalf("unexpected error: err=%v valErrs=%v", err, valErrs)
	}
	if !resp.Accepted || resp.RedactionLevel != "hash-only" || resp.RequestHash == "" {
		t.Fatalf("unexpected response: %+v", resp)
	}
	if !strings.HasPrefix(resp.ProjectID, "geolibre-") {
		t.Fatalf("unexpected project id: %s", resp.ProjectID)
	}
	// Response must never contain raw geometry — struct only has references.
	b, _ := json.Marshal(resp)
	if strings.Contains(string(b), "coordinates") || strings.Contains(string(b), "geometry") {
		t.Fatalf("redacted response leaked geometry: %s", b)
	}

	// Invalid: missing name and datasets.
	_, valErrs, err = svc.BuildGeoLibreProject(ctx, "nasarawa", geogateway.GeoLibreProjectRequest{})
	if err == nil || len(valErrs) != 2 {
		t.Fatalf("expected 2 validation errors, got err=%v valErrs=%v", err, valErrs)
	}

	// Deterministic: same request yields same hash.
	resp2, _, _ := svc.BuildGeoLibreProject(ctx, "nasarawa", geogateway.GeoLibreProjectRequest{
		Name:       "Nasarawa cadastre overview",
		DatasetIDs: []string{"ds-1", "ds-2"},
	})
	if resp2.RequestHash != resp.RequestHash {
		t.Fatalf("expected deterministic hash")
	}
}

// failingPythonClient simulates a Python service seam failure (fail closed).
type failingPythonClient struct{}

func (failingPythonClient) AcceptGeoLibreProject(_ context.Context, _ geogateway.GeoLibreProjectResponse) error {
	return errors.New("python service unreachable")
}

func TestGeoLibreProjectPythonFailure(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	svc.PythonClient = failingPythonClient{}
	_, _, err := svc.BuildGeoLibreProject(context.Background(), "lagos", geogateway.GeoLibreProjectRequest{
		Name:       "x",
		DatasetIDs: []string{"ds-1"},
	})
	if err == nil || !strings.Contains(err.Error(), "rejected") {
		t.Fatalf("expected fail-closed error, got %v", err)
	}
}

func TestGeoLibreProjectHandler(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	routes := geogateway.NewHandler(svc).Routes()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/v1/states/ogun/geospatial/projects/geolibre",
		strings.NewReader(`{"name": "Ogun projects", "dataset_ids": ["ds-9"]}`))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d body %s", rec.Code, rec.Body.String())
	}
	var resp geogateway.GeoLibreProjectResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.TenantStateID != "ogun" || !resp.Accepted {
		t.Fatalf("unexpected response: %+v", resp)
	}

	// Invalid request -> 422.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/v1/states/ogun/geospatial/projects/geolibre",
		strings.NewReader(`{"name": ""}`))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", rec.Code)
	}

	// Disallowed tenant -> 403.
	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodPost, "/v1/states/imo/geospatial/projects/geolibre",
		strings.NewReader(`{"name": "x", "dataset_ids": ["ds-1"]}`))
	routes.ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", rec.Code)
	}
}

func TestUnsupportedCRS(t *testing.T) {
	svc := geogateway.NewLocalService(nil)
	resp := svc.ValidateGeometry(context.Background(), "lagos", geogateway.GeometryValidationRequest{
		Geometry: validPolygon(),
		CRS:      "EPSG:3857",
	})
	if resp.Valid || len(resp.Errors) == 0 || !strings.Contains(resp.Errors[0], "unsupported CRS") {
		t.Fatalf("expected CRS rejection, got %+v", resp)
	}
}
