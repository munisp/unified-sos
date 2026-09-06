package observability

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func testMux() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /items/{id}", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("GET /boom", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	})
	return mux
}

func TestMetricsEndpointFormat(t *testing.T) {
	m := New("mod-test")
	root := http.NewServeMux()
	root.Handle("GET /metrics", m.Handler())
	root.Handle("/", m.Instrument(testMux()))

	req := httptest.NewRequest("GET", "/items/abc", nil)
	root.ServeHTTP(httptest.NewRecorder(), req)
	root.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/boom", nil))

	rec := httptest.NewRecorder()
	root.ServeHTTP(rec, httptest.NewRequest("GET", "/metrics", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("/metrics status = %d", rec.Code)
	}
	body := rec.Body.String()
	for _, want := range []string{
		`service_info{service="mod-test",version="0.1.0"} 1`,
		"# TYPE http_requests_total counter",
		"# TYPE http_request_duration_seconds histogram",
		"# TYPE http_errors_total counter",
		`http_requests_total{method="GET",route="GET /items/{id}",service="mod-test",status="200"} 1`,
		`http_errors_total{method="GET",route="GET /boom",service="mod-test"} 1`,
		`http_request_duration_seconds_count{method="GET",route="GET /items/{id}",service="mod-test"} 1`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metrics body missing %q\n%s", want, body)
		}
	}
	if strings.Contains(body, `route="GET /metrics"`) {
		t.Error("/metrics must not be self-instrumented")
	}
}

func TestRenderDeterministic(t *testing.T) {
	m := New("svc")
	if m.Render() != m.Render() {
		t.Fatal("render must be deterministic")
	}
}
