# RFP Evaluation Scorecards — 1,000-Point QCBS Model

## Methodology

Quality & Cost-Based Selection under ICRC guidelines: **Technical Proposal 700 points (70%)** + **Commercial/PPP Concession Proposal 300 points (30%)**. Minimum technical threshold **560 points (80%)** to qualify for commercial envelope opening.

```
TOTAL COMPOSITE SCORE (1,000) = TECHNICAL (max 700) + COMMERCIAL (max 300)
Commercial formulation: S_comm = (Lowest Acceptable Concession Fee % / Bidded Concession Fee %) × 300
```

## Technical Evaluation Scorecard (700)

### Category 1 — Technical Architecture & Design (250)
| Criterion | Max | Rubric |
|---|---|---|
| System architecture & alignment with SOS spec (microservices, Dapr/Temporal, event-driven streaming, zero-trust, API completeness) | 100 | 90–100% flawless; 70–89% minor sidecar/event-schema gaps; <70% monolithic/proprietary deviations |
| Multi-tenancy, data isolation & NDPA compliance (schema-per-tenant, RLS, automated provisioning, sovereign hosting) | 80 | 70–80% zero-trust + cryptographic isolation; 50–69% shared DB without robust RLS proof; <50% data residency outside Nigeria |
| High availability, DR & performance (active-active, RPO=0 ledger, RTO<1 min, >100k TPS proof) | 70 | 60–70% proven sub-second failover multi-zone; 40–59% basic active-passive; <40% single point of failure |

### Category 2 — Implementation & Delivery Plan (200)
| Criterion | Max | Rubric |
|---|---|---|
| WBS & milestones mapped to WP-01…WP-18, critical path, resource leveling | 80 | 70–80% granular Gantt + sprint cadences; 50–69% high-level only; <50% unrealistic |
| Hardware, field deployment & logistics (weighbridge calibration, POS rollout, GNSS, ANPR mounting, edge connectivity) | 60 | 50–60% full supply-chain backing; 35–49% generic specs; <35% no edge logistics |
| QA, FAT/SAT & security testing (automated CI/CD tests, chaos engineering, pen-test methodology, load scripts, UAT gates) | 60 | 50–60% automated + third-party pen-test SLA; 35–49% mostly manual; <35% vague |

### Category 3 — Human Capital & Local Content (150)
| Criterion | Max | Rubric |
|---|---|---|
| Key personnel: Lead Architect (Go/Rust), Principal GIS Engineer, Ledger Engineer, Lead Security Analyst (CISSP/CISM), PMP Project Director | 80 | 70–80% verified CVs >10 yrs domain; 50–69% qualified with minor gaps; <50% uncertified |
| Nigerian local content & knowledge transfer (civil-service academy, shadow operations, >60% Nigerian engineers) | 70 | 60–70% structured 6-month engineer shadow plan; 40–59% manual handover only; <40% offshore-reliant |

### Category 4 — Past Performance (100)
| Criterion | Max | Rubric |
|---|---|---|
| Verified reference letters from government/enterprise clients for public revenue, GIS, or payment projects | 100 | 90–100% 3+ verified public-sector references; 70–89% 2 enterprise references; <70% none in subnational tech |

**Passing score: 560 / 700.**

## Commercial & PPP Concession Evaluation (300)

| Component | Max | Mechanism |
|---|---|---|
| Concession fee / revenue share % | 150 | Score = (Lowest_Bid_% / Vendor_Bid_%) × 150 |
| CapEx investment commitment & hardware quality | 80 | Higher committed CapEx with top-tier hardware scores higher |
| Tapering / revenue step-down schedule | 40 | Structured fee step-downs as IGR baselines are exceeded |
| Financial escrow & performance bond terms | 30 | Strength of unconditional Tier-1 bank performance bond |

## Bid Submission Compliance Checklist

| # | Document | Standard | M/O |
|---|---|---|---|
| DOC-01 | Certificate of Incorporation & CAC status report | CAMA 2020 | Mandatory |
| DOC-02 | 3-year FIRS Tax Clearance Certificate | 2023–2025 valid | Mandatory |
| DOC-03 | PENCOM, ITF & NSITF compliance certificates | Current year | Mandatory |
| DOC-04 | NDPC Data Protection Compliance License | NDPA 2023 | Mandatory |
| DOC-05 | ISO/IEC 27001 & ISO 22301 certificates | Accredited | Mandatory |
| DOC-06 | Technical architecture proposal & WBS plan | Aligned with SOS spec | Mandatory |
| DOC-07 | Commercial concession fee & IRR model | Excel financial model | Mandatory |
| DOC-08 | Tier-1 bank performance guarantee letter | Unconditional | Mandatory |
