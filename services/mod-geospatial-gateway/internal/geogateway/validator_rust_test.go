package geogateway

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// fixturesDir resolves geospatial/fixtures/wkt relative to this package.
func fixturesDir(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve test file path")
	}
	// internal/geogateway -> mod-geospatial-gateway -> services -> repo root
	root := filepath.Clean(filepath.Join(filepath.Dir(file), "..", "..", "..", ".."))
	return filepath.Join(root, "geospatial", "fixtures", "wkt")
}

func TestGeoJSONToWKTGoldenPolygon(t *testing.T) {
	golden, err := os.ReadFile(filepath.Join(fixturesDir(t), "polygon_square_valid.wkt"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	geom := map[string]any{
		"type": "Polygon",
		"coordinates": []any{
			[]any{
				[]any{8.0, 9.0}, []any{8.01, 9.0}, []any{8.01, 9.01}, []any{8.0, 9.01}, []any{8.0, 9.0},
			},
		},
	}
	wkt, err := GeoJSONToWKT(geom)
	if err != nil {
		t.Fatalf("GeoJSONToWKT: %v", err)
	}
	if wkt != strings.TrimSpace(string(golden)) {
		t.Fatalf("WKT mismatch:\n got: %s\nwant: %s", wkt, strings.TrimSpace(string(golden)))
	}
}

func TestGeoJSONToWKTGoldenMultiPolygon(t *testing.T) {
	golden, err := os.ReadFile(filepath.Join(fixturesDir(t), "multipolygon_valid.wkt"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	ring := func(x0, y0, x1, y1 float64) []any {
		return []any{
			[]any{x0, y0}, []any{x1, y0}, []any{x1, y1}, []any{x0, y1}, []any{x0, y0},
		}
	}
	geom := map[string]any{
		"type":        "MultiPolygon",
		"coordinates": []any{[]any{ring(8, 9, 8.01, 9.01)}, []any{ring(9, 10, 9.02, 10.02)}},
	}
	wkt, err := GeoJSONToWKT(geom)
	if err != nil {
		t.Fatalf("GeoJSONToWKT: %v", err)
	}
	if wkt != strings.TrimSpace(string(golden)) {
		t.Fatalf("WKT mismatch:\n got: %s\nwant: %s", wkt, strings.TrimSpace(string(golden)))
	}
}

func TestGeoJSONToWKTRejectsUnsupported(t *testing.T) {
	if _, err := GeoJSONToWKT(nil); err == nil {
		t.Fatal("expected error for nil geometry")
	}
	if _, err := GeoJSONToWKT(map[string]any{"type": "Point", "coordinates": []any{1.0, 2.0}}); err == nil {
		t.Fatal("expected error for Point geometry")
	}
	if _, err := GeoJSONToWKT(map[string]any{"type": "Polygon"}); err == nil {
		t.Fatal("expected error for missing coordinates")
	}
}

// fakeValidatorScript writes an executable stub emulating the geometry-rs CLI
// JSON report contract.
func fakeValidatorScript(t *testing.T, report string) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("shell-script fake validator requires a POSIX shell")
	}
	path := filepath.Join(t.TempDir(), "fake-validator.sh")
	script := "#!/bin/sh\necho '" + report + "'\n"
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("write fake validator: %v", err)
	}
	return path
}

func TestExecRustValidatorCLIParsesReport(t *testing.T) {
	bin := fakeValidatorScript(t, `{"valid":true,"errors":[],"area":0.0001,"bbox":[8.0,9.0,8.01,9.01]}`)
	cli := &ExecRustValidatorCLI{BinaryPath: bin, Timeout: 5 * time.Second}
	geom := map[string]any{
		"type": "Polygon",
		"coordinates": []any{
			[]any{[]any{8.0, 9.0}, []any{8.01, 9.0}, []any{8.01, 9.01}, []any{8.0, 9.01}, []any{8.0, 9.0}},
		},
	}
	resp, err := cli.ValidateGeoJSON(context.Background(), geom)
	if err != nil {
		t.Fatalf("ValidateGeoJSON: %v", err)
	}
	if !resp.Valid || resp.Validator != "rust-cli" {
		t.Fatalf("unexpected response: %+v", resp)
	}
	if resp.Area != 0.0001 {
		t.Fatalf("area mismatch: %v", resp.Area)
	}
	if resp.BBox == nil || (*resp.BBox)[0] != 8.0 || (*resp.BBox)[3] != 9.01 {
		t.Fatalf("bbox mismatch: %+v", resp.BBox)
	}
}

func TestExecRustValidatorCLIPropagatesInvalidReport(t *testing.T) {
	bin := fakeValidatorScript(t, `{"valid":false,"errors":["polygon 0 ring 0: ring is not closed"],"area":null,"bbox":null}`)
	cli := &ExecRustValidatorCLI{BinaryPath: bin}
	geom := map[string]any{
		"type": "Polygon",
		"coordinates": []any{
			[]any{[]any{8.0, 9.0}, []any{8.01, 9.0}, []any{8.01, 9.01}, []any{8.0, 9.01}},
		},
	}
	resp, err := cli.ValidateGeoJSON(context.Background(), geom)
	if err != nil {
		t.Fatalf("ValidateGeoJSON: %v", err)
	}
	if resp.Valid {
		t.Fatal("expected invalid result")
	}
	if len(resp.Errors) != 1 || !strings.Contains(resp.Errors[0], "not closed") {
		t.Fatalf("unexpected errors: %v", resp.Errors)
	}
}

func TestExecRustValidatorCLIFailsOnMalformedJSON(t *testing.T) {
	bin := fakeValidatorScript(t, `not-json`)
	cli := &ExecRustValidatorCLI{BinaryPath: bin}
	geom := map[string]any{
		"type":        "Polygon",
		"coordinates": []any{[]any{[]any{8.0, 9.0}, []any{8.01, 9.0}, []any{8.01, 9.01}, []any{8.0, 9.0}}},
	}
	if _, err := cli.ValidateGeoJSON(context.Background(), geom); err == nil {
		t.Fatal("expected malformed-report error")
	}
}

func TestExecRustValidatorCLINilBinaryFailsClosed(t *testing.T) {
	var cli *ExecRustValidatorCLI
	if _, err := cli.ValidateGeoJSON(context.Background(), map[string]any{}); err == nil {
		t.Fatal("nil binary must fail closed")
	}
}

func TestNewRustValidatorCLIFailClosedInProduction(t *testing.T) {
	t.Setenv(EnvGatewayMode, "production")
	t.Setenv(EnvRustValidatorBin, "")
	if _, err := NewRustValidatorCLIFromEnv(); err == nil {
		t.Fatal("production without validator binary must fail closed")
	}
	t.Setenv(EnvRustValidatorBin, "/nonexistent/geometry-rs")
	if _, err := NewRustValidatorCLIFromEnv(); err == nil {
		t.Fatal("production with missing binary must fail closed")
	}
}

func TestNewRustValidatorCLILocalDefaultsToInternal(t *testing.T) {
	t.Setenv(EnvGatewayMode, "local")
	t.Setenv(EnvRustValidatorBin, "")
	cli, err := NewRustValidatorCLIFromEnv()
	if err != nil {
		t.Fatalf("local mode must not fail: %v", err)
	}
	if cli != nil {
		t.Fatal("local mode without binary must return nil (internal validator)")
	}
}

func TestNewRustValidatorCLIAcceptsExistingBinary(t *testing.T) {
	t.Setenv(EnvGatewayMode, "production")
	bin := fakeValidatorScript(t, `{"valid":true,"errors":[],"area":null,"bbox":null}`)
	cli, err := NewRustValidatorCLI(bin)
	if err != nil {
		t.Fatalf("existing binary must be accepted: %v", err)
	}
	if cli == nil {
		t.Fatal("expected non-nil CLI")
	}
}

// TestExecRustValidatorCLIAgainstGoldenFixtures runs the real compiled
// geometry-rs binary against the golden WKT fixtures when GEO_GATEWAY_RUST_VALIDATOR_BIN
// points at it; otherwise skipped.
func TestExecRustValidatorCLIAgainstGoldenFixtures(t *testing.T) {
	bin := os.Getenv(EnvRustValidatorBin)
	if bin == "" {
		t.Skip("geometry-rs binary not built; set GEO_GATEWAY_RUST_VALIDATOR_BIN to run")
	}
	cli := &ExecRustValidatorCLI{BinaryPath: bin}

	squareWKT, err := os.ReadFile(filepath.Join(fixturesDir(t), "polygon_square_valid.wkt"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	coords := []any{
		[]any{[]any{8.0, 9.0}, []any{8.01, 9.0}, []any{8.01, 9.01}, []any{8.0, 9.01}, []any{8.0, 9.0}},
	}
	wkt, err := GeoJSONToWKT(map[string]any{"type": "Polygon", "coordinates": coords})
	if err != nil {
		t.Fatalf("GeoJSONToWKT: %v", err)
	}
	if wkt != strings.TrimSpace(string(squareWKT)) {
		t.Fatalf("fixture WKT mismatch: %q vs %q", wkt, squareWKT)
	}
	resp, err := cli.ValidateGeoJSON(context.Background(), map[string]any{"type": "Polygon", "coordinates": coords})
	if err != nil {
		t.Fatalf("ValidateGeoJSON: %v", err)
	}
	if !resp.Valid {
		t.Fatalf("expected valid golden polygon, got errors: %v", resp.Errors)
	}

	// The unclosed-ring fixture must be reported invalid by the real binary.
	if _, err := os.ReadFile(filepath.Join(fixturesDir(t), "polygon_unclosed_ring.wkt")); err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	resp2, err := cli.ValidateGeoJSON(context.Background(), map[string]any{
		"type": "Polygon",
		"coordinates": []any{
			[]any{[]any{8.0, 9.0}, []any{8.01, 9.0}, []any{8.01, 9.01}, []any{8.0, 9.01}},
		},
	})
	if err != nil {
		t.Fatalf("ValidateGeoJSON: %v", err)
	}
	if resp2.Valid {
		t.Fatal("expected invalid for unclosed ring")
	}
}
