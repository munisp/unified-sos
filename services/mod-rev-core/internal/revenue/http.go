package revenue

import (
	"bytes"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strconv"
	"sync"

	"github.com/munisp/unified-sos/ledger/splits"
)

// maxRequestBodyBytes bounds JSON request bodies (DoS hardening); the
// largest contract payload (an assessment with metadata) is a few KB.
const maxRequestBodyBytes = 1 << 20 // 1 MiB

// jsonBufPool recycles response encode buffers on the hot path (webhook
// settlements + assessment reads) to avoid per-request allocations.
var jsonBufPool = sync.Pool{New: func() any { return new(bytes.Buffer) }}

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
	mux.HandleFunc("POST /api/v1/states/{state_id}/revenue/refunds", h.refund)
	mux.HandleFunc("POST /api/v1/states/{state_id}/revenue/corrections", h.correction)
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
	if err := decodeJSON(w, r, &req); err != nil {
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
	if err := decodeJSON(w, r, &req); err != nil {
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

// refund handles POST /v1/refunds: dual-controlled reversal of a settled bill.
func (h *Handler) refund(w http.ResponseWriter, r *http.Request) {
	state, ok := stateParam(w, r)
	if !ok {
		return
	}
	var req RefundRequest
	if err := decodeJSON(w, r, &req); err != nil {
		writeError(w, badRequest("MALFORMED_JSON", "request body: %v", err))
		return
	}
	if req.BillReference == "" {
		writeError(w, badRequest("INVALID_REFUND", "bill_reference is required"))
		return
	}
	resp, err := h.svc.Refund(state, &req)
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

// correction handles POST /v1/corrections: dual-controlled compensating
// chain for a misallocated payment.
func (h *Handler) correction(w http.ResponseWriter, r *http.Request) {
	state, ok := stateParam(w, r)
	if !ok {
		return
	}
	var req CorrectionRequest
	if err := decodeJSON(w, r, &req); err != nil {
		writeError(w, badRequest("MALFORMED_JSON", "request body: %v", err))
		return
	}
	if req.BillReference == "" {
		writeError(w, badRequest("INVALID_CORRECTION", "bill_reference is required"))
		return
	}
	resp, err := h.svc.CorrectMisallocation(state, &req)
	if err != nil {
		writeError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, resp)
}

// decodeJSON reads a bounded JSON request body (http.MaxBytesReader caps the
// size so an oversized body is rejected with a decode error instead of being
// buffered without limit).
func decodeJSON(w http.ResponseWriter, r *http.Request, dst any) error {
	r.Body = http.MaxBytesReader(w, r.Body, maxRequestBodyBytes)
	return json.NewDecoder(r.Body).Decode(dst)
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	buf := jsonBufPool.Get().(*bytes.Buffer)
	buf.Reset()
	defer jsonBufPool.Put(buf)
	if err := json.NewEncoder(buf).Encode(body); err != nil {
		log.Printf("mod-rev-core: encode response: %v", err)
		writeError(w, internalError("INTERNAL", "encode response: %v", err))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", strconv.Itoa(buf.Len()))
	w.WriteHeader(status)
	if _, err := w.Write(buf.Bytes()); err != nil {
		log.Printf("mod-rev-core: write response: %v", err)
	}
}

func writeError(w http.ResponseWriter, err error) {
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		apiErr = internalError("INTERNAL", "%v", err)
	}
	writeJSON(w, apiErr.Status, ErrorResponse{Error: APIErrorBody{Code: apiErr.Code, Message: apiErr.Message}})
}
