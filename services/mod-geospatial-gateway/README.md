# mod-geospatial-gateway

Low-latency geospatial API/command service (Go 1.23, standard library only) for
geometry validation and processing-job dispatch. Stage 5 component per
`SPEC-GEOSPATIAL.md` section 4.2.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Liveness probe. |
| POST | `/v1/states/{state}/geospatial/validate-geometry` | Validate a GeoJSON Polygon/MultiPolygon: coordinate bounds (-180..180, -90..90), ring closure, minimum ring points, duplicate consecutive vertices, non-zero area. Returns approximate planar (shoelace) area and bbox. |
| POST | `/v1/states/{state}/geospatial/jobs` | Validate and enqueue a processing job (`UNASSESSED_PROPERTY_JOIN`, `NDVI_CHANGE_DETECTION`, `H3_AGGREGATION`, `GEOPARQUET_EXPORT`, `GEOLIBRE_PROJECT_BUILD`) through an injectable dispatcher. |
| GET | `/v1/states/{state}/geospatial/jobs/{job_id}` | Retrieve a job from an injectable store, scoped to the tenant. |
| POST | `/v1/states/{state}/geospatial/projects/geolibre` | Validate a GeoLibre project request and return the redacted (hash-only, reference-only) request accepted by the Python `mod-geospatial` service. Never carries raw geometry, PII, or credentials. |

## Tenant allowlist

Only these state tenants are accepted (403 otherwise):
`lagos`, `ogun`, `osun`, `benue`, `nasarawa`, `taraba`.

The job store is tenant-scoped: a job created under one tenant cannot be read
under another (404).

## Architecture and seams

- `internal/geogateway/validators.go` — deterministic internal validator
  (default). No external dependencies.
- `internal/geogateway/service.go` — seams:
  - `GeometryValidator` — validation engine (default: `InternalValidator`).
  - `JobDispatcher` — job enqueueing (default: in-memory `LocalDispatcher`).
  - `JobStore` — job retrieval (default: in-memory `InMemoryJobStore`).
  - `PythonServiceClient` — Python `mod-geospatial` client seam (default:
    `LocalPythonClient`, no network calls).
  - `RustValidatorCLI` — optional Rust geometry validator binary seam; when
    configured and unavailable, the service fails closed.
- Production deployments inject real dispatcher/store/Python/Rust
  implementations; local and test modes are fully deterministic with no
  external calls.

## Run

```bash
go build ./...
GATEWAY_ADDR=:8014 ./server   # or: go run ./cmd/server
```

Docker (distroless, non-root):

```bash
docker build -t mod-geospatial-gateway .
docker run -p 8014:8014 mod-geospatial-gateway
```

## Test

```bash
gofmt -l .
go vet ./...
go test ./...
```

Tests cover the tenant allowlist, tenant isolation of the job store, valid and
invalid geometry (ring closure, coordinate bounds, minimum points, duplicate
vertices, zero area, multipolygon), job dispatch (including failure), GeoLibre
project redaction, and handler status codes. No external calls are made.

## Contract

No FastAPI app exists for this service; the API contract is this README
(per spec section 5).
