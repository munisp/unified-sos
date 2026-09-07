# Cilium + eBPF layer for the SOS platform

This directory is the network-security and observability substrate for the
Nigerian State Operating System (SOS) — 29 services, 37 state tenants, one
helm release per state (`infra/helm/sos-platform`). Cilium is the CNI and
eBPF dataplane; it sits *under* the existing stack (APISIX gateway, Keycloak
OIDC, OpenAppSec WAF) and does not replace any of them.

## Why Cilium / eBPF for this platform

1. **Kernel-level enforcement without sidecars.** The revenue path targets
   millions of assessment/payment transactions per second across 37 states.
   A sidecar service mesh adds ~1–2 ms and an extra userspace hop per
   service call per pod. eBPF enforces policy in the kernel on the direct
   pod-to-pod path — zero extra proxies, zero per-pod CPU/memory tax. For a
   ledger-critical platform running TigerBeetle quorums and KEDA-scaled
   consumers, that overhead reduction is the difference between hitting and
   missing the TPS target at state-DC hardware budgets.
2. **kube-proxy replacement.** `kubeProxyReplacement=true` removes iptables
   from the data path. Service load balancing becomes O(1) eBPF map lookups
   instead of O(n) iptables chains — at 29 services × tens of replicas × 37
   tenants this keeps north-south and east-west latency flat as the mesh
   grows, and removes iptables-reload stalls during KEDA scale events.
3. **Identity-aware L3–L7 policy.** Plain Kubernetes `NetworkPolicy` is
   IP/port based and cannot express "only the API gateway may call
   `POST /cad/v1/arms-register`". `CiliumNetworkPolicy` selects on pod
   identity (labels / service accounts), survives pod rescheduling, and
   adds L7 HTTP method+path rules plus FQDN-aware egress (NIBSS, Mojaloop,
   NIMC/CAC) that IP-based policy cannot express.
4. **Hubble flow observability.** Hubble rides the same eBPF hooks and gives
   per-flow visibility (verdicts, drops, DNS, HTTP latency) across every
   inter-service call — essential for auditing cross-tenant traffic in
   shared-tier clusters and for proving NDPA data-flow compliance.
5. **Tetragon runtime enforcement.** eBPF-based runtime security: block
   unexpected `execve` in payment/ledger pods, watch file integrity on
   `/etc` and ledger data dirs, and audit privilege escalation — without
   an agent in every pod.
6. **Multi-tenant fit.** Native-routing mode on bare-metal state data
   centres avoids VXLAN encapsulation overhead; tunnel mode is available
   where L3 reachability between nodes is not guaranteed. Bandwidth
   manager enforces per-pod fairness so one tenant's KEDA burst cannot
   starve a co-tenant on shared hardware.

## Layout

| Path | Purpose |
| --- | --- |
| `install/values.yaml` | Helm values for the official Cilium chart (kube-proxy replacement, Hubble, Tetragon, IPAM, tier profiles). |
| `policies/` | `CiliumNetworkPolicy` / `CiliumClusterwideNetworkPolicy` zero-trust set: default-deny, tenant isolation, data-plane guards, DNS visibility, L7 API rules, FQDN egress allowlist. |
| `tetragon/` | `TracingPolicy` runtime enforcement + NDPA/security-baseline mapping notes. |
| `observability/` | Hubble metrics scrape config and Grafana dashboard JSON (flow drops, policy verdicts, DNS, HTTP latency). |
| `validate_cilium.py` | Pytest suite: YAML parses, policies reference real services, selector+rules present, no empty files. |

## Install (summary)

```sh
helm repo add cilium https://helm.cilium.io/
helm install cilium cilium/cilium --version 1.16.5 \
  --namespace kube-system -f deploy/cilium/install/values.yaml
kubectl apply -f deploy/cilium/policies/
kubectl apply -f deploy/cilium/tetragon/
```

Rollout order, failure modes and the non-overlap statement vs APISIX /
Keycloak / OpenAppSec are in `docs/security/cilium-ebpf.md`.
