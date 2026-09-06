package geogateway

import (
	"fmt"
	"math"
	"strings"
)

const (
	minRingPoints   = 4 // closed ring: at least 3 distinct points + closure
	minPolygonArea  = 1e-12
	maxAbsLongitude = 180.0
	maxAbsLatitude  = 90.0
)

// allowedTenants is the tenant allowlist for Stage 5.
var allowedTenants = map[string]bool{
	"lagos":    true,
	"ogun":     true,
	"osun":     true,
	"benue":    true,
	"nasarawa": true,
	"taraba":   true,
}

// AllowedTenants returns the sorted tenant allowlist.
func AllowedTenants() []string {
	out := make([]string, 0, len(allowedTenants))
	for t := range allowedTenants {
		out = append(out, t)
	}
	// simple insertion sort to avoid importing sort for 6 items
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

// TenantAllowed reports whether state is in the tenant allowlist.
func TenantAllowed(state string) bool {
	return allowedTenants[strings.ToLower(strings.TrimSpace(state))]
}

// Ring is a linear ring of [lon, lat] positions.
type Ring [][]float64

// Polygon is an outer ring plus zero or more holes.
type Polygon []Ring

// InternalValidator is the deterministic local geometry validator. It is the
// default seam implementation when no Rust validator CLI is configured.
type InternalValidator struct{}

// Name identifies the validator engine.
func (InternalValidator) Name() string { return "internal" }

// ValidateGeoJSON validates a GeoJSON Polygon or MultiPolygon geometry object
// (decoded as map[string]any). It returns validation errors, the approximate
// planar shoelace area, and the bounding box.
func (InternalValidator) ValidateGeoJSON(geom map[string]any) (errors []string, area float64, bbox *BBox, geomType string) {
	if geom == nil {
		return []string{"geometry is required"}, 0, nil, ""
	}
	t, _ := geom["type"].(string)
	coords, ok := geom["coordinates"]
	if !ok {
		return []string{"geometry.coordinates is required"}, 0, nil, t
	}
	switch t {
	case "Polygon":
		poly, err := parsePolygon(coords)
		if err != nil {
			return []string{err.Error()}, 0, nil, t
		}
		return validatePolygons([]Polygon{poly})
	case "MultiPolygon":
		raw, ok := coords.([]any)
		if !ok || len(raw) == 0 {
			return []string{"multipolygon coordinates must be a non-empty array"}, 0, nil, t
		}
		polys := make([]Polygon, 0, len(raw))
		for i, p := range raw {
			poly, err := parsePolygon(p)
			if err != nil {
				errors = append(errors, fmt.Sprintf("polygon %d: %s", i, err.Error()))
				continue
			}
			polys = append(polys, poly)
		}
		if len(polys) == 0 {
			if len(errors) == 0 {
				errors = append(errors, "no valid polygons found")
			}
			return errors, 0, nil, t
		}
		vErrs, area, bbox, _ := validatePolygons(polys)
		return append(errors, vErrs...), area, bbox, t
	default:
		return []string{fmt.Sprintf("unsupported geometry type %q: only Polygon and MultiPolygon are accepted", t)}, 0, nil, t
	}
}

// parsePolygon decodes a GeoJSON polygon coordinate array into rings.
func parsePolygon(coords any) (Polygon, error) {
	rawRings, ok := coords.([]any)
	if !ok || len(rawRings) == 0 {
		return nil, fmt.Errorf("polygon coordinates must be a non-empty array of linear rings")
	}
	poly := make(Polygon, 0, len(rawRings))
	for i, rr := range rawRings {
		rawPts, ok := rr.([]any)
		if !ok {
			return nil, fmt.Errorf("ring %d must be an array of positions", i)
		}
		ring := make(Ring, 0, len(rawPts))
		for j, rp := range rawPts {
			pos, ok := rp.([]any)
			if !ok || len(pos) < 2 {
				return nil, fmt.Errorf("ring %d position %d must be [lon, lat]", i, j)
			}
			lon, ok1 := toFloat(pos[0])
			lat, ok2 := toFloat(pos[1])
			if !ok1 || !ok2 {
				return nil, fmt.Errorf("ring %d position %d must be numeric [lon, lat]", i, j)
			}
			ring = append(ring, []float64{lon, lat})
		}
		poly = append(poly, ring)
	}
	return poly, nil
}

func toFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	default:
		return 0, false
	}
}

// validatePolygons checks rings and computes total shoelace area (holes
// subtracted) and overall bbox.
func validatePolygons(polys []Polygon) (errors []string, area float64, bbox *BBox, _ string) {
	b := &BBox{math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)}
	for pi, poly := range polys {
		for ri, ring := range poly {
			label := fmt.Sprintf("polygon %d ring %d", pi, ri)
			if len(ring) < minRingPoints {
				errors = append(errors, fmt.Sprintf("%s: ring must have at least %d positions (got %d)", label, minRingPoints, len(ring)))
			}
			for i, pt := range ring {
				if math.Abs(pt[0]) > maxAbsLongitude {
					errors = append(errors, fmt.Sprintf("%s: position %d longitude %.6f out of range [-180, 180]", label, i, pt[0]))
				}
				if math.Abs(pt[1]) > maxAbsLatitude {
					errors = append(errors, fmt.Sprintf("%s: position %d latitude %.6f out of range [-90, 90]", label, i, pt[1]))
				}
				if i > 0 && pt[0] == ring[i-1][0] && pt[1] == ring[i-1][1] {
					errors = append(errors, fmt.Sprintf("%s: duplicate consecutive vertices at position %d", label, i))
				}
				if pt[0] < b[0] {
					b[0] = pt[0]
				}
				if pt[1] < b[1] {
					b[1] = pt[1]
				}
				if pt[0] > b[2] {
					b[2] = pt[0]
				}
				if pt[1] > b[3] {
					b[3] = pt[1]
				}
			}
			if len(ring) >= 2 && (ring[0][0] != ring[len(ring)-1][0] || ring[0][1] != ring[len(ring)-1][1]) {
				errors = append(errors, fmt.Sprintf("%s: ring is not closed (first and last positions differ)", label))
			}
			// Shoelace area: outer ring adds, holes subtract.
			a := shoelaceArea(ring)
			if ri == 0 {
				if a < minPolygonArea {
					errors = append(errors, fmt.Sprintf("%s: outer ring has (near) zero area", label))
				}
				area += a
			} else {
				area -= a
			}
		}
	}
	if len(errors) > 0 {
		return errors, 0, b, ""
	}
	return nil, area, b, ""
}

// shoelaceArea computes the absolute planar area of a ring.
func shoelaceArea(ring Ring) float64 {
	if len(ring) < 2 {
		return 0
	}
	var sum float64
	for i := 0; i < len(ring)-1; i++ {
		sum += ring[i][0]*ring[i+1][1] - ring[i+1][0]*ring[i][1]
	}
	return math.Abs(sum) / 2
}
