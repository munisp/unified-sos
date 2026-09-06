# Executive Overview & Architectural Context

## The Problem

Nigerian subnational governments face severe structural challenges: volatile federal statutory allocations (FAAC), escalating debt service burdens, manual revenue collection leakages, and widespread vendor lock-in caused by unintegrated software point solutions procured independently by each Ministry, Department, and Agency (MDA).

## The Platform Answer

The **State Operating System (SOS)** is an open-source, multi-tenant digital backbone designed to operate across all 36 Nigerian States and the FCT, piloted across six states (Lagos, Ogun, Osun, Benue, Nasarawa, Taraba) spanning the full fiscal spectrum from ₦17.46bn to ₦1.26trn annual IGR [LIVE].

**Canonical Platform Concept:** SOS delivers a single hardened application foundation where:
- Each **State Government** operates as an isolated sovereign tenant.
- Each **MDA** operates as a sub-tenant / bounded business domain.
- **Dynamic policy packs** (JSON form schemas, localized fee schedules, statutory multi-leg revenue split formulas, OPA Rego rules) adapt the system instantly to state-specific laws — e.g., the Benue State Internal Revenue Administration Law, Lagos Land Use Charge Law, or Ogun Land Administration and Revenue Management System — **without recompiling microservices**.

## System Design Goals

1. **Sovereign State Multi-Tenancy** — complete logical, network, and cryptographic isolation between states.
2. **Immutable Financial Correctness** — every monetary transaction strictly double-entry ledgered with deterministic zero-loss accounting.
3. **Subnational Edge Resilience** — POS terminals, weighbridges, and border toll gates operate offline during rural connectivity drops and synchronize reliably upon reconnection.
4. **Extensibility via Dynamic Policy Packs** — adding a revenue stream, cadastral tariff, or statutory deduction requires uploading a declarative JSON schema, not modifying binaries.
5. **Zero Vendor Lock-In** — 100% open-source core deployable on sovereign state-owned data centers, Galaxy Backbone, or commercial clouds.

## Cross-State Platform Commonality

While revenue instruments vary (lithium in Nasarawa, cocoa in Osun, port haulage in Lagos), the underlying computational workflow is identical:

```
Identity Verification → Geofenced Spatial Assertion → Double-Entry Assessment
→ Payment Clearing → Real-Time Statutory Revenue Split
```

This commonality is what makes one operating system viable across the six-state spectrum: **the same seven modules appear in all six states; only configuration depth and lead module change** [DERIVED].
