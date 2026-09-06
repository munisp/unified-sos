package geogateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// Environment configuration for the Rust validator CLI seam.
const (
	// EnvRustValidatorBin is the path to the compiled geometry-rs validator
	// binary (reads WKT via --wkt, emits a JSON report on stdout).
	EnvRustValidatorBin = "GEO_GATEWAY_RUST_VALIDATOR_BIN"
	// EnvGatewayMode selects the runtime mode; "production" enforces
	// fail-closed behaviour when the validator binary is unavailable.
	EnvGatewayMode = "GEO_GATEWAY_MODE"
)

func isProductionMode() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv(EnvGatewayMode)), "production")
}

// rustCLIReport mirrors the JSON report emitted by the geometry-rs CLI:
// {"valid":bool,"errors":[string],"area":float|null,"bbox":[4]float|null}.
type rustCLIReport struct {
	Valid  bool        `json:"valid"`
	Errors []string    `json:"errors"`
	Area   *float64    `json:"area"`
	BBox   *[4]float64 `json:"bbox"`
}

// ExecRustValidatorCLI implements RustValidatorCLI by executing the compiled
// geometry-rs validator binary and parsing its JSON report. The geometry is
// serialised to WKT and passed via --wkt.
type ExecRustValidatorCLI struct {
	BinaryPath string
	Timeout    time.Duration // defaults to 5s
}

// NewRustValidatorCLI builds the CLI seam from configuration. It fails closed
// in production mode when no binary path is configured; in local/test mode a
// missing binary yields (nil, nil) so callers use the internal validator.
func NewRustValidatorCLI(binaryPath string) (RustValidatorCLI, error) {
	binaryPath = strings.TrimSpace(binaryPath)
	if binaryPath == "" {
		if isProductionMode() {
			return nil, fmt.Errorf("%s is not set; Rust validator fails closed in production", EnvRustValidatorBin)
		}
		return nil, nil
	}
	if _, err := os.Stat(binaryPath); err != nil {
		if isProductionMode() {
			return nil, fmt.Errorf("rust validator binary unavailable: %w", err)
		}
		return nil, nil
	}
	return &ExecRustValidatorCLI{BinaryPath: binaryPath}, nil
}

// NewRustValidatorCLIFromEnv wires the seam from GEO_GATEWAY_RUST_VALIDATOR_BIN.
func NewRustValidatorCLIFromEnv() (RustValidatorCLI, error) {
	return NewRustValidatorCLI(os.Getenv(EnvRustValidatorBin))
}

// ValidateGeoJSON converts the GeoJSON geometry to WKT, executes the Rust
// binary, and parses its JSON report into a GeometryValidationResponse.
func (c *ExecRustValidatorCLI) ValidateGeoJSON(ctx context.Context, geom map[string]any) (GeometryValidationResponse, error) {
	if c == nil || c.BinaryPath == "" {
		return GeometryValidationResponse{}, errors.New("rust validator binary is not configured")
	}
	wkt, err := GeoJSONToWKT(geom)
	if err != nil {
		return GeometryValidationResponse{}, err
	}
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	runCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(runCtx, c.BinaryPath, "--wkt", wkt)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if runCtx.Err() == context.DeadlineExceeded {
			return GeometryValidationResponse{}, fmt.Errorf("rust validator timed out after %s", timeout)
		}
		return GeometryValidationResponse{}, fmt.Errorf("rust validator failed: %v: %s", err, strings.TrimSpace(stderr.String()))
	}
	var report rustCLIReport
	if err := json.Unmarshal(stdout.Bytes(), &report); err != nil {
		return GeometryValidationResponse{}, fmt.Errorf("rust validator returned malformed report: %w", err)
	}
	resp := GeometryValidationResponse{
		Valid:     report.Valid,
		Errors:    report.Errors,
		Validator: "rust-cli",
	}
	if report.Area != nil {
		resp.Area = *report.Area
	}
	if report.BBox != nil {
		b := BBox(*report.BBox)
		resp.BBox = &b
	}
	return resp, nil
}

// GeoJSONToWKT serialises a GeoJSON Polygon or MultiPolygon geometry object
// to WKT (lon/lat axis order, matching the geometry-rs CLI input contract).
func GeoJSONToWKT(geom map[string]any) (string, error) {
	if geom == nil {
		return "", errors.New("geometry is required")
	}
	t, _ := geom["type"].(string)
	coords, ok := geom["coordinates"]
	if !ok {
		return "", errors.New("geometry.coordinates is required")
	}
	switch t {
	case "Polygon":
		poly, err := parsePolygon(coords)
		if err != nil {
			return "", err
		}
		return "POLYGON(" + ringsWKT(poly) + ")", nil
	case "MultiPolygon":
		raw, ok := coords.([]any)
		if !ok || len(raw) == 0 {
			return "", errors.New("multipolygon coordinates must be a non-empty array")
		}
		parts := make([]string, 0, len(raw))
		for i, p := range raw {
			poly, err := parsePolygon(p)
			if err != nil {
				return "", fmt.Errorf("polygon %d: %w", i, err)
			}
			parts = append(parts, "("+ringsWKT(poly)+")")
		}
		return "MULTIPOLYGON(" + strings.Join(parts, ", ") + ")", nil
	default:
		return "", fmt.Errorf("unsupported geometry type %q: only Polygon and MultiPolygon are accepted", t)
	}
}

func ringsWKT(poly Polygon) string {
	rings := make([]string, 0, len(poly))
	for _, ring := range poly {
		pts := make([]string, 0, len(ring))
		for _, pt := range ring {
			pts = append(pts, fmt.Sprintf("%g %g", pt[0], pt[1]))
		}
		rings = append(rings, "("+strings.Join(pts, ", ")+")")
	}
	return strings.Join(rings, ", ")
}
