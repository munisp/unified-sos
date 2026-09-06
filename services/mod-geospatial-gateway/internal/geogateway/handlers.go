package geogateway

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strings"
)

// Handler exposes the geospatial gateway HTTP API.
type Handler struct {
	Svc *Service
}

// NewHandler constructs a Handler for the given service.
func NewHandler(svc *Service) *Handler { return &Handler{Svc: svc} }

// Routes returns the service mux.
func (h *Handler) Routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", h.healthz)
	mux.HandleFunc("POST /v1/states/{state}/geospatial/validate-geometry", h.validateGeometry)
	mux.HandleFunc("POST /v1/states/{state}/geospatial/jobs", h.createJob)
	mux.HandleFunc("GET /v1/states/{state}/geospatial/jobs/{job_id}", h.getJob)
	mux.HandleFunc("POST /v1/states/{state}/geospatial/projects/geolibre", h.buildGeoLibreProject)
	return mux
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("encode response: %v", err)
	}
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, ErrorResponse{Error: msg})
}

// tenantFromRequest extracts and validates the {state} path value against the
// tenant allowlist, writing a 404/403 style error on failure.
func tenantFromRequest(w http.ResponseWriter, r *http.Request) (string, bool) {
	state := strings.ToLower(strings.TrimSpace(r.PathValue("state")))
	if state == "" {
		writeError(w, http.StatusBadRequest, "state path parameter is required")
		return "", false
	}
	if !TenantAllowed(state) {
		writeError(w, http.StatusForbidden, "tenant state is not in the geospatial allowlist")
		return "", false
	}
	return state, true
}

func decodeJSON(w http.ResponseWriter, r *http.Request, dst any) bool {
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body: "+err.Error())
		return false
	}
	return true
}

func (h *Handler) healthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (h *Handler) validateGeometry(w http.ResponseWriter, r *http.Request) {
	tenant, ok := tenantFromRequest(w, r)
	if !ok {
		return
	}
	var req GeometryValidationRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	resp := h.Svc.ValidateGeometry(r.Context(), tenant, req)
	status := http.StatusOK
	if !resp.Valid {
		status = http.StatusUnprocessableEntity
	}
	writeJSON(w, status, resp)
}

func (h *Handler) createJob(w http.ResponseWriter, r *http.Request) {
	tenant, ok := tenantFromRequest(w, r)
	if !ok {
		return
	}
	var req CreateJobRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	job, valErrs, err := h.Svc.CreateJob(r.Context(), tenant, req)
	if valErrs != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]any{"error": "invalid job request", "details": valErrs})
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusAccepted, job)
}

func (h *Handler) getJob(w http.ResponseWriter, r *http.Request) {
	tenant, ok := tenantFromRequest(w, r)
	if !ok {
		return
	}
	jobID := r.PathValue("job_id")
	if jobID == "" {
		writeError(w, http.StatusBadRequest, "job_id path parameter is required")
		return
	}
	job, found := h.Svc.GetJob(r.Context(), tenant, jobID)
	if !found {
		writeError(w, http.StatusNotFound, errors.New("job not found for tenant").Error())
		return
	}
	writeJSON(w, http.StatusOK, job)
}

func (h *Handler) buildGeoLibreProject(w http.ResponseWriter, r *http.Request) {
	tenant, ok := tenantFromRequest(w, r)
	if !ok {
		return
	}
	var req GeoLibreProjectRequest
	if !decodeJSON(w, r, &req) {
		return
	}
	resp, valErrs, err := h.Svc.BuildGeoLibreProject(r.Context(), tenant, req)
	if valErrs != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]any{"error": "invalid project request", "details": valErrs})
		return
	}
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusAccepted, resp)
}
