# Runbook: Audit Chain Tamper Alert

**Trigger:** `sosctl audit verify-chain` hash mismatch, OpenSearch WORM
snapshot verification failure, or the DR gate reporting a manifest
hash mismatch on the audit archive.

**This is a security incident.** Treat as suspected tamper until proven
otherwise. See `SECURITY.md`.

1. **Preserve evidence.** Do not restart or redeploy the control plane.
   Snapshot the current local archive files (`LocalFileArchive` JSONL) and
   export the affected OpenSearch indices to a forensic copy (read-only).
2. **Localise the break.** `sosctl audit verify-chain --tenant <state>`
   prints the first broken link (event id, expected vs actual prev-hash).
   Determine whether the break is in the local file archive, the
   OpenSearch archive, or both:
   - Local-only break → the local append path or its host is suspect.
   - OpenSearch-only break → impossible under WORM unless the repository
     credentials were abused; escalate immediately.
3. **Isolate.** Revoke the `sos-secrets-opensearch` credentials (rotate
   per `docs/operations/secrets-management.md` §Incident response) and the
   control-plane service account pending investigation.
4. **Reconstruct.** Restore the last known-good WORM snapshot into a
   scratch cluster (`tools/backup/opensearch_snapshot.py --execute
   --verify-only`, then `_restore` on a scratch OpenSearch) and diff
   event-by-event to establish the true chain.
5. **Report.** NDPA 2023 breach-assessment duty: the audit archive holds
   personal-data processing records — involve the DPO within 24 h if
   records were altered or exfiltrated.
6. **Resume** only after the chain verifies end-to-end on the
   reconstructed archive and the platform lead signs off.
