# syntax=docker/dockerfile:1
# sos-mod-rev-core — core revenue service (STIN registry, assessments, splits).
# NOTE: build context must be the REPOSITORY ROOT because go.mod carries a
# monorepo-local `replace` into ../../ledger/splits:
#   docker build -f services/mod-rev-core/Dockerfile -t ghcr.io/munisp/mod-rev-core .

FROM golang:1.23 AS build

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-rev-core" \
      org.opencontainers.image.description="SOS core revenue service — STIN taxpayer registry, assessments, gazetted revenue splits (TigerBeetle-backed)"

WORKDIR /src

# Copy the module and its monorepo-local dependency first for layer caching.
COPY ledger/splits/ ledger/splits/
COPY services/mod-rev-core/go.mod services/mod-rev-core/go.sum* services/mod-rev-core/
WORKDIR /src/services/mod-rev-core
RUN go mod download

COPY services/mod-rev-core/ .

RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/mod-rev-core ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot AS runtime

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-rev-core" \
      org.opencontainers.image.description="SOS core revenue service — STIN taxpayer registry, assessments, gazetted revenue splits (TigerBeetle-backed)"

COPY --from=build /out/mod-rev-core /usr/local/bin/mod-rev-core

USER nonroot:nonroot

EXPOSE 8080

# REV_CORE_ADDR (default :8080), REV_CORE_POLICY_DIR, REV_CORE_LEDGER, TB_ADDRESSES
ENV REV_CORE_ADDR=":8080" \
    REV_CORE_LEDGER="memory"

ENTRYPOINT ["/usr/local/bin/mod-rev-core"]
