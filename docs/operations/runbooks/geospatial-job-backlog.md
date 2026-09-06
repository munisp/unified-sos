# Runbook: Geospatial Job Backlog

**Trigger:** KEDA ScaledObject for the geospatial workers
(`infra/helm/sos-platform/templates/keda-scaledobject.yaml`) at max
replicas with queue depth still growing; or tile/join latency SLO breach
on mod-geospatial / mod-gis-* services.

1. **Measure.** Queue depth by job type (tiling, Sedona spatial joins,
   parcel recompute). Check worker pods: crash loops, OOMKills, or CPU
   throttling are the usual causes — not load.
2. **Crash loop path.** Read the failing job id from worker logs; replay
   it in isolation on a dev worker (`geospatial/` harness). A poison job
   (corrupt GeoTIFF, out-of-bounds parcel geometry) must be quarantined:
   mark the job `quarantined` in the control plane so the queue drains —
   do not delete it (audit requirement).
3. **Resource path.** If workers are healthy but slow, scale the KEDA
   `maxReplicaCount` for the affected state's overlay within the
   ResourceQuota ceiling; Tier-1 (Lagos) has dedicated nodes — do not
   burst shared-tier tenants into the Lagos pool.
4. **Data-plane path.** Sedona join slowness with healthy workers usually
   means a skewed join key (one LGA with a very large parcel set).
   Repartition the job (see `tests/load/sedona_joins.py --profile local`
   for a reproducer) rather than throwing more workers at it.
5. **Tenant isolation check.** A backlog must never starve other states:
   verify per-tenant queue quotas held (one runaway tenant = one full
   queue, not a platform outage).
6. **Drain & verify.** After the fix, watch depth return to zero; re-run
   the geospatial smoke test; record the poison jobs and their
   disposition in the incident log.
