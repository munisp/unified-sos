// Package geogateway implements the mod-geospatial-gateway service: a
// low-latency API/command service for geospatial geometry validation and
// processing-job dispatch for Nigerian state tenants.
package geogateway

import (
	"crypto/sha256"
	"encoding/hex"
	"time"
)

// JobType enumerates the supported geospatial processing job kinds.
type JobType string

const (
	JobTypeUnassessedPropertyJoin JobType = "UNASSESSED_PROPERTY_JOIN"
	JobTypeNDVIChangeDetection    JobType = "NDVI_CHANGE_DETECTION"
	JobTypeH3Aggregation          JobType = "H3_AGGREGATION"
	JobTypeGeoparquetExport       JobType = "GEOPARQUET_EXPORT"
	JobTypeGeolibreProjectBuild   JobType = "GEOLIBRE_PROJECT_BUILD"
)

// ValidJobTypes returns the allowlist of accepted job types.
func ValidJobTypes() []JobType {
	return []JobType{
		JobTypeUnassessedPropertyJoin,
		JobTypeNDVIChangeDetection,
		JobTypeH3Aggregation,
		JobTypeGeoparquetExport,
		JobTypeGeolibreProjectBuild,
	}
}

func (t JobType) valid() bool {
	for _, v := range ValidJobTypes() {
		if t == v {
			return true
		}
	}
	return false
}

// JobStatus is the processing job state machine value.
type JobStatus string

const (
	JobStatusQueued JobStatus = "QUEUED"
)

// Job is a geospatial processing job record.
type Job struct {
	JobID         string            `json:"job_id"`
	TenantStateID string            `json:"tenant_state_id"`
	JobType       JobType           `json:"job_type"`
	Status        JobStatus         `json:"status"`
	Parameters    map[string]string `json:"parameters,omitempty"`
	CreatedAt     time.Time         `json:"created_at"`
}

// GeometryValidationRequest is the payload for validate-geometry.
type GeometryValidationRequest struct {
	// Geometry is a GeoJSON Polygon or MultiPolygon object.
	Geometry map[string]any `json:"geometry"`
	// CRS is optional; only EPSG:4326-style lon/lat coordinates are supported.
	CRS string `json:"crs,omitempty"`
}

// BBox is a bounding box [minLon, minLat, maxLon, maxLat].
type BBox [4]float64

// GeometryValidationResponse reports deterministic validation results.
type GeometryValidationResponse struct {
	Valid    bool     `json:"valid"`
	Errors   []string `json:"errors"`
	Area     float64  `json:"area"`
	BBox     *BBox    `json:"bbox,omitempty"`
	Geometry string   `json:"geometry_type,omitempty"`
	// Validator identifies which engine produced the result
	// ("internal", "rust-cli", ...).
	Validator string `json:"validator"`
}

// CreateJobRequest is the payload for enqueueing a processing job.
type CreateJobRequest struct {
	JobType    JobType           `json:"job_type"`
	Parameters map[string]string `json:"parameters,omitempty"`
}

// GeoLibreProjectRequest is the payload accepted by the gateway and forwarded
// (in production) to the Python mod-geospatial service. It must never carry
// raw geometry, PII, or credentials — only references.
type GeoLibreProjectRequest struct {
	Name        string   `json:"name"`
	DatasetIDs  []string `json:"dataset_ids"`
	SourceJobID string   `json:"source_job_id,omitempty"`
}

// GeoLibreProjectResponse is the redacted project request accepted by the
// Python service. It contains hashes and references only.
type GeoLibreProjectResponse struct {
	ProjectID      string   `json:"project_id"`
	TenantStateID  string   `json:"tenant_state_id"`
	Name           string   `json:"name"`
	DatasetIDs     []string `json:"dataset_ids"`
	SourceJobID    string   `json:"source_job_id,omitempty"`
	RedactionLevel string   `json:"redaction_level"`
	RequestHash    string   `json:"request_hash"`
	Accepted       bool     `json:"accepted"`
}

// ErrorResponse is the standard error envelope.
type ErrorResponse struct {
	Error string `json:"error"`
}

// hashRequest produces a stable hash of redacted request fields.
func hashRequest(parts ...string) string {
	h := sha256.New()
	for _, p := range parts {
		h.Write([]byte(p))
		h.Write([]byte{0})
	}
	return hex.EncodeToString(h.Sum(nil))
}
