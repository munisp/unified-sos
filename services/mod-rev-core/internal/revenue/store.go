package revenue

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sync"
)

// Store is the repository interface for mod-rev-core persistence. The
// default implementation is an in-memory, per-state partitioned store
// used by tests and local development; the production implementation is
// the PostgreSQL adapter against db/migrations/0002_revenue_core.sql
// (documented stub — see README; balances never live in SQL, ADR-002).
type Store interface {
	// CreateAssessment persists a new assessment. Returns ErrConflict if
	// the bill reference already exists.
	CreateAssessment(a *Assessment) error
	// GetAssessment fetches one assessment by ID within a state tenant.
	GetAssessment(state, assessmentID string) (*Assessment, bool)
	// GetAssessmentByBill fetches one assessment by bill reference
	// within a state tenant.
	GetAssessmentByBill(state, billRef string) (*Assessment, bool)
	// GetByIdempotencyKey returns the assessment created for this
	// idempotency key within a state tenant, if any.
	GetByIdempotencyKey(state, key string) (*Assessment, bool)
	// MarkPaid transitions an assessment ISSUED → PAID. Returns false if
	// the assessment is not in ISSUED state.
	MarkPaid(state, assessmentID string, paidAtUnix int64) bool
	// ApplyPayment accumulates a (partial) payment: AmountPaidKobo grows
	// by amount and the status moves ISSUED/PARTIALLY_PAID →
	// PARTIALLY_PAID or, once AmountPaidKobo reaches AmountDueKobo, PAID.
	// Returns the updated assessment and false if the assessment is not
	// in a payable state.
	ApplyPayment(state, assessmentID string, amount, paidAtUnix int64) (*Assessment, bool)
	// SetStatus force-transitions an assessment (e.g. PAID → REFUNDED).
	// Returns false if the assessment does not exist.
	SetStatus(state, assessmentID, status string) bool
	// UpdateRevenueHead re-points an assessment at the corrected revenue
	// head after an approved correction.
	UpdateRevenueHead(state, assessmentID, revenueHead string) bool
	// SaveSettlement persists (upserts) the settlement record for a bill.
	// It MUST be called before the settlement chain is submitted so a
	// crash/retry can replay the recorded outcome.
	SaveSettlement(rec *SettlementRecord)
	// GetSettlement returns the settlement record for a bill, if any.
	GetSettlement(state, billRef string) (*SettlementRecord, bool)
	// RecordPayment registers a payment provider reference; it returns
	// false if this reference was already applied (webhook redelivery).
	RecordPayment(state, providerRef string) bool
	// AppendAudit appends a hash-chained audit event and returns the
	// stored event (Seq/PrevHash/Hash filled in).
	AppendAudit(ev AuditEvent) AuditEvent
	// AuditTrail returns the hash-chained audit events for a state.
	AuditTrail(state string) []AuditEvent
	// UpsertTaxpayer registers the taxpayer on first sight.
	UpsertTaxpayer(t *Taxpayer)
	// GetTaxpayer fetches a taxpayer by STIN within a state tenant.
	GetTaxpayer(state, stin string) (*Taxpayer, bool)
	// NextSequence returns the next per-state assessment sequence number.
	NextSequence(state string) uint64
}

// InMemoryStore is a goroutine-safe Store partitioned by state tenant.
type InMemoryStore struct {
	mu            sync.Mutex
	byState       map[string]map[string]*Assessment       // state → assessmentID → record
	byBill        map[string]map[string]string            // state → billRef → assessmentID
	byIdempotency map[string]map[string]string            // state → key → assessmentID
	taxpayers     map[string]map[string]*Taxpayer         // state → STIN → record
	settlements   map[string]map[string]*SettlementRecord // state → billRef → record
	payments      map[string]map[string]bool              // state → providerRef → applied
	audits        map[string][]AuditEvent                 // state → hash-chained events
	sequences     map[string]uint64
}

// NewInMemoryStore returns an empty store.
func NewInMemoryStore() *InMemoryStore {
	return &InMemoryStore{
		byState:       make(map[string]map[string]*Assessment),
		byBill:        make(map[string]map[string]string),
		byIdempotency: make(map[string]map[string]string),
		taxpayers:     make(map[string]map[string]*Taxpayer),
		settlements:   make(map[string]map[string]*SettlementRecord),
		payments:      make(map[string]map[string]bool),
		audits:        make(map[string][]AuditEvent),
		sequences:     make(map[string]uint64),
	}
}

func (s *InMemoryStore) stateBucket(state string) map[string]*Assessment {
	b, ok := s.byState[state]
	if !ok {
		b = make(map[string]*Assessment)
		s.byState[state] = b
	}
	return b
}

// CreateAssessment implements Store.
func (s *InMemoryStore) CreateAssessment(a *Assessment) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.byBill[a.State] == nil {
		s.byBill[a.State] = make(map[string]string)
	}
	if s.byIdempotency[a.State] == nil {
		s.byIdempotency[a.State] = make(map[string]string)
	}
	if _, dup := s.byBill[a.State][a.BillReference]; dup {
		return ErrConflict
	}
	s.stateBucket(a.State)[a.AssessmentID] = a
	s.byBill[a.State][a.BillReference] = a.AssessmentID
	if a.IdempotencyKey != "" {
		s.byIdempotency[a.State][a.IdempotencyKey] = a.AssessmentID
	}
	return nil
}

// GetAssessment implements Store.
func (s *InMemoryStore) GetAssessment(state, assessmentID string) (*Assessment, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byState[state][assessmentID]
	return a, ok
}

// GetAssessmentByBill implements Store.
func (s *InMemoryStore) GetAssessmentByBill(state, billRef string) (*Assessment, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	id, ok := s.byBill[state][billRef]
	if !ok {
		return nil, false
	}
	a, ok := s.byState[state][id]
	return a, ok
}

// GetByIdempotencyKey implements Store.
func (s *InMemoryStore) GetByIdempotencyKey(state, key string) (*Assessment, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	id, ok := s.byIdempotency[state][key]
	if !ok {
		return nil, false
	}
	a, ok := s.byState[state][id]
	return a, ok
}

// MarkPaid implements Store.
func (s *InMemoryStore) MarkPaid(state, assessmentID string, paidAtUnix int64) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byState[state][assessmentID]
	if !ok || a.Status != StatusIssued {
		return false
	}
	a.Status = StatusPaid
	t := timeFromUnix(paidAtUnix)
	a.PaidAt = &t
	return true
}

// ApplyPayment implements Store.
func (s *InMemoryStore) ApplyPayment(state, assessmentID string, amount, paidAtUnix int64) (*Assessment, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byState[state][assessmentID]
	if !ok || (a.Status != StatusIssued && a.Status != StatusPartiallyPaid) {
		return nil, false
	}
	a.AmountPaidKobo += amount
	if a.AmountPaidKobo >= a.AmountDueKobo {
		a.Status = StatusPaid
		t := timeFromUnix(paidAtUnix)
		a.PaidAt = &t
	} else {
		a.Status = StatusPartiallyPaid
	}
	cp := *a
	return &cp, true
}

// SetStatus implements Store.
func (s *InMemoryStore) SetStatus(state, assessmentID, status string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byState[state][assessmentID]
	if !ok {
		return false
	}
	a.Status = status
	return true
}

// UpdateRevenueHead implements Store.
func (s *InMemoryStore) UpdateRevenueHead(state, assessmentID, revenueHead string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	a, ok := s.byState[state][assessmentID]
	if !ok {
		return false
	}
	a.RevenueHead = revenueHead
	return true
}

// SaveSettlement implements Store (upsert by bill reference).
func (s *InMemoryStore) SaveSettlement(rec *SettlementRecord) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.settlements[rec.TenantState] == nil {
		s.settlements[rec.TenantState] = make(map[string]*SettlementRecord)
	}
	s.settlements[rec.TenantState][rec.BillReference] = rec
}

// GetSettlement implements Store.
func (s *InMemoryStore) GetSettlement(state, billRef string) (*SettlementRecord, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, ok := s.settlements[state][billRef]
	return rec, ok
}

// RecordPayment implements Store.
func (s *InMemoryStore) RecordPayment(state, providerRef string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.payments[state] == nil {
		s.payments[state] = make(map[string]bool)
	}
	if s.payments[state][providerRef] {
		return false
	}
	s.payments[state][providerRef] = true
	return true
}

// auditHash computes the tamper-evident hash of one audit event.
func auditHash(prevHash, actor, action, billRef, detail, timestamp string, seq uint64) string {
	sum := sha256.Sum256([]byte(fmt.Sprintf("%s|%s|%s|%s|%s|%s|%d",
		prevHash, actor, action, billRef, detail, timestamp, seq)))
	return hex.EncodeToString(sum[:])
}

// AppendAudit implements Store: the event is chained onto the state's
// audit log (PrevHash ← previous event's Hash).
func (s *InMemoryStore) AppendAudit(ev AuditEvent) AuditEvent {
	s.mu.Lock()
	defer s.mu.Unlock()
	trail := s.audits[ev.TenantState]
	ev.Seq = uint64(len(trail)) + 1
	ev.PrevHash = ""
	if len(trail) > 0 {
		ev.PrevHash = trail[len(trail)-1].Hash
	}
	ev.Hash = auditHash(ev.PrevHash, ev.Actor, ev.Action, ev.BillReference, ev.Detail, ev.Timestamp, ev.Seq)
	s.audits[ev.TenantState] = append(trail, ev)
	return ev
}

// AuditTrail implements Store.
func (s *InMemoryStore) AuditTrail(state string) []AuditEvent {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]AuditEvent, len(s.audits[state]))
	copy(out, s.audits[state])
	return out
}

// UpsertTaxpayer implements Store.
func (s *InMemoryStore) UpsertTaxpayer(t *Taxpayer) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.taxpayers[t.State] == nil {
		s.taxpayers[t.State] = make(map[string]*Taxpayer)
	}
	if _, ok := s.taxpayers[t.State][t.STIN]; !ok {
		s.taxpayers[t.State][t.STIN] = t
	}
}

// GetTaxpayer implements Store.
func (s *InMemoryStore) GetTaxpayer(state, stin string) (*Taxpayer, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.taxpayers[state][stin]
	return t, ok
}

// NextSequence implements Store.
func (s *InMemoryStore) NextSequence(state string) uint64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sequences[state]++
	return s.sequences[state]
}
