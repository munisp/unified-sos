package revenue

import "sync"

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
	byState       map[string]map[string]*Assessment // state → assessmentID → record
	byBill        map[string]map[string]string      // state → billRef → assessmentID
	byIdempotency map[string]map[string]string      // state → key → assessmentID
	taxpayers     map[string]map[string]*Taxpayer   // state → STIN → record
	sequences     map[string]uint64
}

// NewInMemoryStore returns an empty store.
func NewInMemoryStore() *InMemoryStore {
	return &InMemoryStore{
		byState:       make(map[string]map[string]*Assessment),
		byBill:        make(map[string]map[string]string),
		byIdempotency: make(map[string]map[string]string),
		taxpayers:     make(map[string]map[string]*Taxpayer),
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
