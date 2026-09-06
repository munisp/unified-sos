package revenue

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"

	"github.com/munisp/unified-sos/ledger/splits"
)

// Handler exposes the revenue service over HTTP per
// contracts/openapi/revenue-assessments.yaml (Go 1.22 pattern routing;
// bearer JWT validation is performed by the APISIX gateway upstream —
// see README).
type Handler struct {
	svc *Service
}

// NewHandler builds the HTTP adapter.
func NewHandler(svc *Service) *Handler { return &Handler{svc: svc} }

// Routes returns the service mux.
func (h *Handler) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", h.healthz)
	mux.HandleFunc("POST /api/v1/states/{state_id}/revenue/assessments", h.createAssessment)
	mux.HandleFunc("GET /api/v1/states/{state_id}/revenue/assessments/{assessment_id}", h.getAssessment)
	mux.HandleFunc("POST /api/v1/states/{state_id}/revenue/payments/webhook", h.paymentWebhook)
	return mux
}

func (h *Handler) healthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "service": "mod-rev-core"})
}

// stateParam extracts and validates the {state_id} path parameter
// against the contract enum — the tenant-isolation entry point.
func stateParam(w http.ResponseWriter, r *http.Request) (string, bool) {
	state := r.PathValue("state_id")
	if !splits.ValidStateTenant(state) {
		writeError(w, badRequest("INVALID_STATE_TENANT",
			"state_id %q not in {lagos, ogun, osun, benue, nasarawa, taraba}", state))
		return "", false
	}
	return state, true
}

func (h *Handler) createAssessment(w http.ResponseWriter, r *http.Request) {
	state, ok := stateParam(w, r)
	if !ok {
		return
	}
	var req AssessmentRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, badRequest("MALFORMED_JSON", "request body: %v", err))
		return
	}
	resp, created, err := h.svc.CreateAssessment(state, r.Header.Get("Idempotency-Key"), &req)
	if err != nil {
		writeError(w, err)
		return
	}
	status := http.StatusOK
	if created {
		status = http.StatusCreated
	}
	writeJSON(w, status, resp)
}

func (h *Handler) getAssessment(w http.ResponseWriter, r *http.Request) {
	state, ok := stateParam(w, r)
	if !ok {
		return
	}
	resp, err := h.svc.GetAssessment(state, r.PathValue("assessment_id"))
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

func (h *Handler) paymentWebhook(w http.ResponseWriter, r *http.Request) {
	state, ok := stateParam(w, r)
	if !ok {
		return
	}
	var req SettlementRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, badRequest("MALFORMED_JSON", "request body: %v", err))
		return
	}
	if req.BillReference == "" || req.AmountKobo <= 0 {
		writeError(w, badRequest("INVALID_SETTLEMENT", "bill_reference and positive amount_kobo are required"))
		return
	}
	resp, err := h.svc.SettleBill(state, &req)
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		log.Printf("mod-rev-core: encode response: %v", err)
	}
}

func writeError(w http.ResponseWriter, err error) {
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		apiErr = internalError("INTERNAL", "%v", err)
	}
	writeJSON(w, apiErr.Status, ErrorResponse{Error: APIErrorBody{Code: apiErr.Code, Message: apiErr.Message}})
}
