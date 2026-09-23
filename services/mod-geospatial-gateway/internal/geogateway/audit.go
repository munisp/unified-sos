package geogateway

// Hash-chained tenant audit log (P1 audit immutability), the Go counterpart of
// the Python modules' app/audit.py and the reference idiom in
// services/mod-gis-lands/lands_app/eventlog.py: every key mutating operation
// appends an event to an append-only hash chain — each event carries the
// EventHash of its predecessor, so tampering, deletion, or reordering is
// detected by AuditLog.Verify.
//
// Canonical form: encoding/json marshals map[string]any with sorted keys,
// matching the Python canonical_json(sort_keys=True) convention.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

// GenesisPrevHash is the PrevHash of the first (genesis) record in the chain.
const GenesisPrevHash = "0000000000000000000000000000000000000000000000000000000000000000"

// AuditEvent is one immutable entry in the hash-chained audit log.
type AuditEvent struct {
	EventID       string         `json:"event_id"`
	EventType     string         `json:"event_type"`
	TenantStateID string         `json:"tenant_state_id"`
	Actor         string         `json:"actor"`
	Detail        map[string]any `json:"detail"`
	RecordedAt    string         `json:"recorded_at"`
	PrevHash      string         `json:"prev_hash"`
	EventHash     string         `json:"event_hash"`
}

// payload returns the hashed view (prev_hash/event_hash excluded).
func (e AuditEvent) payload() map[string]any {
	return map[string]any{
		"event_id":        e.EventID,
		"event_type":      e.EventType,
		"tenant_state_id": e.TenantStateID,
		"actor":           e.Actor,
		"detail":          e.Detail,
		"recorded_at":     e.RecordedAt,
	}
}

// canonicalPayload marshals the exact byte sequence the map-based canonical
// form produces (encoding/json sorts map keys: actor, detail, event_id,
// event_type, prev_hash, recorded_at, tenant_state_id) without allocating a
// map or sorting keys on every append/verify. Field order below IS the
// canonical order; do not reorder.
type canonicalPayload struct {
	Actor         string         `json:"actor"`
	Detail        map[string]any `json:"detail"`
	EventID       string         `json:"event_id"`
	EventType     string         `json:"event_type"`
	PrevHash      string         `json:"prev_hash"`
	RecordedAt    string         `json:"recorded_at"`
	TenantStateID string         `json:"tenant_state_id"`
}

// eventHash computes the chained hash of one event. Byte-identical to
// EventPayloadHash(e.payload(), e.PrevHash) (pinned by a differential test).
func eventHash(e *AuditEvent) string {
	raw, _ := json.Marshal(canonicalPayload{
		Actor:         e.Actor,
		Detail:        e.Detail,
		EventID:       e.EventID,
		EventType:     e.EventType,
		PrevHash:      e.PrevHash,
		RecordedAt:    e.RecordedAt,
		TenantStateID: e.TenantStateID,
	})
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

// EventPayloadHash hashes one record's payload chained to prevHash.
func EventPayloadHash(payload map[string]any, prevHash string) string {
	body := map[string]any{}
	for k, v := range payload {
		if k == "prev_hash" || k == "event_hash" {
			continue
		}
		body[k] = v
	}
	body["prev_hash"] = prevHash
	raw, _ := json.Marshal(body) // map[string]any marshals deterministically (sorted keys)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

// AuditLog is an append-only, tenant-scoped, hash-chained audit log
// (in-memory twin of the append-only audit table; no UPDATE/DELETE grants).
type AuditLog struct {
	mu     sync.Mutex
	events []AuditEvent
	seq    int
}

// NewAuditLog constructs an empty audit log.
func NewAuditLog() *AuditLog { return &AuditLog{} }

// Record appends an event, chained to the previous event's hash.
func (l *AuditLog) Record(eventType, tenantStateID, actor string, detail map[string]any) AuditEvent {
	l.mu.Lock()
	defer l.mu.Unlock()
	prevHash := GenesisPrevHash
	if len(l.events) > 0 {
		prevHash = l.events[len(l.events)-1].EventHash
	}
	l.seq++
	e := AuditEvent{
		EventID:       fmt.Sprintf("geogateway-audit-%06d", l.seq),
		EventType:     eventType,
		TenantStateID: tenantStateID,
		Actor:         actor,
		Detail:        detail,
		RecordedAt:    time.Now().UTC().Format(time.RFC3339Nano),
		PrevHash:      prevHash,
	}
	if e.Detail == nil {
		e.Detail = map[string]any{}
	}
	e.EventHash = eventHash(&e)
	l.events = append(l.events, e)
	return e
}

// Events returns a tenant-scoped (RLS-equivalent) read of the chain.
func (l *AuditLog) Events(tenantStateID string) []AuditEvent {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := []AuditEvent{}
	for _, e := range l.events {
		if tenantStateID == "" || e.TenantStateID == tenantStateID {
			out = append(out, e)
		}
	}
	return out
}

// Verify recomputes the whole chain; an empty slice means intact.
func (l *AuditLog) Verify() []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	errs := []string{}
	lastHash := ""
	for i, e := range l.events {
		expectedPrev := GenesisPrevHash
		if lastHash != "" {
			expectedPrev = lastHash
		}
		if e.PrevHash != expectedPrev {
			errs = append(errs, fmt.Sprintf("event %s: broken chain link", e.EventID))
		}
		if e.EventHash != eventHash(&e) {
			errs = append(errs, fmt.Sprintf("event %s: hash mismatch — record tampered", e.EventID))
		}
		lastHash = e.EventHash
		_ = i
	}
	return errs
}

// verifyAudit serves GET /api/v1/states/{state_id}/audit/verify.
func (h *Handler) verifyAudit(w http.ResponseWriter, r *http.Request) {
	state := strings.ToLower(strings.TrimSpace(r.PathValue("state_id")))
	if state == "" {
		writeError(w, http.StatusBadRequest, "state_id path parameter is required")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"valid":   len(h.Svc.Audit.Verify()) == 0,
		"entries": len(h.Svc.Audit.Events(state)),
	})
}
