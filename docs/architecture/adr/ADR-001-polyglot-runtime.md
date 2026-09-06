# ADR-001: Polyglot Service Runtime

**Status:** Accepted · **Domain:** Runtime & Service Architecture

## Decision

Go, Rust, and Python services orchestrated via **Dapr sidecars**.

- **Go** — high-throughput I/O REST/gRPC API gateways and business controllers.
- **Rust** — TigerBeetle client bindings, IoT sensor decoders, Fluvio streaming actors.
- **Python** — GIS pipelines, Ray AI, Apache Sedona spatial batch processing.

## Rationale

Each language maps to its strength: Go for concurrent network services, Rust for memory-safe high-performance edge/ledger bindings, Python for the geospatial/ML ecosystem.

## Tradeoff

Multi-language build pipelines managed through strict Bazel/Nix container image standards; single Harbor registry with Cosign-signed images enforces uniformity.
