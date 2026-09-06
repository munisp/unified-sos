// Package observability provides zero-dependency Prometheus-style metrics
// for the Go services (Stage 7.C): a stdlib-only counter/histogram registry,
// HTTP middleware, and a /metrics text-exposition handler. Equivalent to
// services/_shared/observability.py used by the FastAPI services.
package observability

import (
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Buckets matches the Python services' histogram buckets (seconds).
var Buckets = []float64{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10}

type histogram struct {
	buckets []int64 // len(Buckets)+1, last is +Inf
	sum     float64
	count   int64
}

// Metrics is a thread-safe in-process registry.
type Metrics struct {
	mu        sync.Mutex
	service   string
	requests  map[string]int64 // key: route|method|status
	errors    map[string]int64 // key: route|method
	durations map[string]*histogram
}

// New returns a registry for the named service.
func New(service string) *Metrics {
	return &Metrics{
		service:   service,
		requests:  map[string]int64{},
		errors:    map[string]int64{},
		durations: map[string]*histogram{},
	}
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

// Instrument wraps next, recording request count, latency, and 5xx errors.
// The Go 1.22+ ServeMux matched pattern (r.Pattern) is used as the
// low-cardinality route label; /metrics is excluded.
func (m *Metrics) Instrument(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/metrics" {
			next.ServeHTTP(w, r)
			return
		}
		start := time.Now()
		sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(sw, r)
		elapsed := time.Since(start).Seconds()

		route := r.Pattern
		if route == "" {
			route = r.Method + " " + r.URL.Path
		}
		m.mu.Lock()
		m.requests[route+"|"+r.Method+"|"+strconv.Itoa(sw.status)]++
		h, ok := m.durations[route+"|"+r.Method]
		if !ok {
			h = &histogram{buckets: make([]int64, len(Buckets)+1)}
			m.durations[route+"|"+r.Method] = h
		}
		for i, b := range Buckets {
			if elapsed <= b {
				h.buckets[i]++
			}
		}
		h.buckets[len(Buckets)]++
		h.sum += elapsed
		h.count++
		if sw.status >= 500 {
			m.errors[route+"|"+r.Method]++
		}
		m.mu.Unlock()
	})
}

// Handler serves the Prometheus text exposition at /metrics.
func (m *Metrics) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		fmt.Fprint(w, m.Render())
	})
}

func num(v float64) string {
	if v == float64(int64(v)) {
		return strconv.FormatInt(int64(v), 10)
	}
	return strconv.FormatFloat(v, 'f', -1, 64)
}

func labels(pairs ...string) string {
	if len(pairs) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteByte('{')
	for i := 0; i < len(pairs); i += 2 {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "%s=%q", pairs[i], pairs[i+1])
	}
	b.WriteByte('}')
	return b.String()
}

// Render produces the text exposition format (deterministic ordering).
func (m *Metrics) Render() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	var b strings.Builder
	b.WriteString("# HELP service_info Static service identity gauge (always 1).\n")
	b.WriteString("# TYPE service_info gauge\n")
	fmt.Fprintf(&b, "service_info%s 1\n", labels("service", m.service, "version", "0.1.0"))

	b.WriteString("# HELP http_requests_total Total HTTP requests by route/method/status.\n")
	b.WriteString("# TYPE http_requests_total counter\n")
	reqKeys := make([]string, 0, len(m.requests))
	for k := range m.requests {
		reqKeys = append(reqKeys, k)
	}
	sort.Strings(reqKeys)
	for _, k := range reqKeys {
		parts := strings.Split(k, "|")
		fmt.Fprintf(&b, "http_requests_total%s %d\n",
			labels("method", parts[1], "route", parts[0], "service", m.service, "status", parts[2]),
			m.requests[k])
	}

	b.WriteString("# HELP http_errors_total HTTP 5xx responses by route/method.\n")
	b.WriteString("# TYPE http_errors_total counter\n")
	errKeys := make([]string, 0, len(m.errors))
	for k := range m.errors {
		errKeys = append(errKeys, k)
	}
	sort.Strings(errKeys)
	for _, k := range errKeys {
		parts := strings.Split(k, "|")
		fmt.Fprintf(&b, "http_errors_total%s %d\n",
			labels("method", parts[1], "route", parts[0], "service", m.service), m.errors[k])
	}

	b.WriteString("# HELP http_request_duration_seconds Request latency histogram.\n")
	b.WriteString("# TYPE http_request_duration_seconds histogram\n")
	hKeys := make([]string, 0, len(m.durations))
	for k := range m.durations {
		hKeys = append(hKeys, k)
	}
	sort.Strings(hKeys)
	for _, k := range hKeys {
		parts := strings.Split(k, "|")
		h := m.durations[k]
		base := []string{"method", parts[1], "route", parts[0], "service", m.service}
		var cumulative int64
		for i, bound := range Buckets {
			cumulative += h.buckets[i]
			fmt.Fprintf(&b, "http_request_duration_seconds_bucket%s %d\n",
				labels(append(base, "le", num(bound))...), cumulative)
		}
		cumulative += h.buckets[len(Buckets)]
		fmt.Fprintf(&b, "http_request_duration_seconds_bucket%s %d\n",
			labels(append(base, "le", "+Inf")...), cumulative)
		fmt.Fprintf(&b, "http_request_duration_seconds_sum%s %s\n", labels(base...), strconv.FormatFloat(h.sum, 'f', 6, 64))
		fmt.Fprintf(&b, "http_request_duration_seconds_count%s %d\n", labels(base...), h.count)
	}
	return b.String()
}
