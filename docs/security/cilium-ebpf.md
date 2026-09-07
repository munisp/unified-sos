# Cilium + eBPF integration architecture

How the Cilium/eBPF layer fits under the existing SOS platform stack, how it
rolls out across the 37 state tenants, and what it does and does **not**
replace. Implementation lives in `deploy/cilium/`.

## 1. Layered security model — explicit non-overlap

The platform already has application-layer and edge controls. Cilium adds
the network-identity and runtime layers underneath them. Each layer has a
distinct trust domain:

| Layer | Component | Responsibility | What Cilium does NOT do |
| --- | --- | --- | --- |
| L7 app identity | **Keycloak (OIDC)** | Who the *user/principal* is; token issuance, realm per state | Cilium never authenticates users. OIDC tokens still govern every API call; network policy only decides which *workloads* may connect. |
| Edge gateway | **APISIX** | Routing, quotas, TLS termination, per-state rate limits | Cilium does not route APIs or terminate public TLS. It guarantees that traffic reaching a service *came through* the gateway (L7 path rules), so APISIX policy cannot be bypassed in-cluster. |
| WAF | **OpenAppSec** | Payload inspection, OWASP attack blocking at the edge | Cilium's L7 HTTP rules are method/path allowlists only — no payload inspection, no WAF logic. OpenAppSec stays the WAF. |
| L3/L4 + L7-network identity | **Cilium + eBPF** | Which *workload identity* may talk to which workload, on which port/path; FQDN egress; kernel-level enforcement | New layer — previously only flat cluster networking + no tenant isolation at the wire. |
| Runtime enforcement | **Tetragon (eBPF)** | Process/file integrity in payment & ledger pods; privilege-escalation audit | Complements, not replaces, the static gates in `tests/security/test_security_baseline.py` (PII egress, tenant isolation, secrets, webhook auth). |

Non-overlap statement: **Cilium enforces network identity and runtime
behavior; Keycloak owns user identity, APISIX owns API routing, OpenAppSec
owns payload security.** Removing Cilium never weakens authN/Z logic (it
fails closed at the network layer instead); adding Cilium never bypasses or
relaxes OIDC, gateway quotas, or WAF inspection.

## 2. Data-path architecture

```
 citizen/NIBSS/NIMC/CAC
        │  TLS 1.3
   APISIX gateway (sos-gateway)  ← OpenAppSec WAF inline
        │  Cilium L7 policy: only api-gateway identity may reach
        │  sensitive paths (/cad/v1/arms-register, /ml/v1/*, /rev/v1/*, /payments/v1/*)
   tenant modules (sos-tenant-<state>)  ← default-deny; no cross-tenant flows
        │  labeled access only (sos.gov.ng/data-access: postgres|tigerbeetle|redis|kafka)
   data plane (sos-data): postgres, tigerbeetle, redis, kafka
        │  FQDN egress allowlist: NIBSS, Mojaloop, NIMC, CAC
   national rails
```

- **kube-proxy replacement** removes iptables from this path entirely;
  service LB is an eBPF map lookup. KEDA scale-out bursts no longer stall
  on iptables reloads.
- **Hubble** observes every flow above (verdict, DNS, HTTP latency) and
  exports metrics to the existing Prometheus/Grafana
  (`deploy/observability/` + `deploy/cilium/observability/`).
- **Tetragon** watches exec/file/capability events in payment and ledger
  pods and streams NDPA-relevant audit events to OpenSearch.

## 3. Rollout plan

### Phase 0 — staging, audit mode
1. Install Cilium on a shared-tier staging cluster with
   `policyAuditMode: true` and Tetragon policies using `Post` instead of
   `Sigkill`.
2. Run `pytest deploy/cilium/validate_cilium.py` in CI (parses + service
   reference gates) and `pytest tests/security/test_security_baseline.py`
   before and after — both must stay green.
3. Soak one week; compare Hubble "would-deny" verdicts against expected
   flows; tune Tetragon allow-binary lists.

### Phase 1 — shared tier first
1. Shared-tier clusters carry the most tenants and the least per-tenant
   hardware — they benefit most from sidecar-free enforcement and bandwidth
   manager fairness. Keep tunnel (VXLAN) routing here; cloud/overlay
   networks don't guarantee pod-CIDR L3 reachability.
2. Apply `default-deny` → `dns-visibility` → `data-plane-access` →
   `tenant-isolation` → `l7-api-rules` → `egress-allowlist`, in that order,
   watching the "Cross-tenant flow attempts" panel (must read zero before
   proceeding).
3. Enable one state tenant at a time; the shared tier can host a
   policy-enabled and a legacy tenant simultaneously because policies are
   namespace-scoped.

### Phase 2 — dedicated tier with native routing
1. Tier-1/2 states on bare-metal DC hardware: switch to
   `routingMode: native` + `autoDirectNodeRoutes` (requires routable pod
   CIDRs — coordinate with the state DC network team, or peer via BGP).
   Native routing removes VXLAN overhead on the ledger path.
2. Enable `tetragon.export.filename` file-based export with node-local
   rotation for air-gapped-ish DCs.
3. TigerBeetle 5-replica quorum here; verify VSR replica-to-replica rules
   (`tigerbeetle-access` self-ingress) before scaling.

## 4. Performance expectations

| Concern | Service mesh w/ sidecars | Cilium/eBPF |
| --- | --- | --- |
| Extra hop per service call | 2 proxy hops (~0.5–2 ms) | none — kernel path |
| Per-pod memory tax | ~50–150 MB/proxy | ~0 (agent is per-node) |
| Service LB | iptables O(n) | eBPF map O(1) |
| Policy enforcement | userspace proxy | socket/XDP hooks |
| Flow observability | mesh-specific metrics | Hubble on same hooks |

Expected impact on the millions-TPS revenue target: the payment path
(mod-rev-core ↔ tigerbeetle ↔ kafka) keeps native pod-to-pod latency;
policy checks add sub-microsecond map lookups. VXLAN adds ~5–10% throughput
cost on shared tier — eliminated on dedicated tier via native routing.
Bandwidth manager caps noisy-neighbor risk during KEDA bursts (sacrificing
peak burst TPS of one tenant for fleet-wide fairness).

## 5. Failure modes & fail-closed posture

| Failure | Behavior |
| --- | --- |
| Cilium agent down on a node | `agentNotReadyTaintKey` keeps the node tainted — pods are not scheduled onto an unenforced node. Existing flows keep last-programmed eBPF maps (no silent open-up). |
| Policy CRD deleted | Pods revert to `policyEnforcementMode: default` isolation — still default-deny via the catch-all policies; if *all* policies vanish, Cilium's default-isolation keeps selected pods closed. |
| Hubble/relay outage | Enforcement unaffected (separate path); observability degrades — alert `CiliumAgentUnreachable`. |
| Tetragon crash | Exec/file enforcement stops but network policy remains; `tetragon` scrape target goes down → alert. Restart is per-node DaemonSet. |
| FQDN egress DNS poisoning attempt | toFQDN pins to DNS-observed IPs from kube-dns only; rogue resolvers are blocked by dns-visibility policy. |
| Hibernation/cold start storm | 2× operator/relay replicas, generous startup probes, `maxUnavailable: 1` rolling updates — node pools can power-cycle without policy gaps. |

Design principle: **every failure degrades toward denial, never toward
open.** The only audit-mode knob (`policyAuditMode`) is a rollout tool and
must be `false` in production values.
