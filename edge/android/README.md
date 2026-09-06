# edge/android — Android Keystore / StrongBox signer shell

Reference Kotlin binding for POS-terminal device signing backed by Android
Keystore (StrongBox where available). The private Ed25519 key is generated
and held inside the hardware-backed keystore and is never exportable;
`KeystoreSigner` is the only code path that touches it.

## Format contract (must match `edge/edge-daemon` exactly)

Interop is pinned by
`edge/edge-daemon/tests/fixtures/android_sig_vectors.json` and
`edge/edge-daemon/tests/test_android_vectors.py`:

* **Signing bytes** — `SignedRecord.signing_bytes()`:
  `json.dumps({device_id, sequence, payload}, sort_keys=True,
  separators=(",", ":"))` encoded UTF-8. In Kotlin this is
  `KeystoreSigner.canonicalSigningBytes(deviceId, sequence, payloadJson)`,
  which re-serializes the payload with sorted keys and no whitespace.
* **Signature** — raw Ed25519 over exactly those bytes, base64url-encoded
  (with padding, matching Python `base64.urlsafe_b64encode`).
* **Public key** — raw 32-byte Ed25519 key, base64url-encoded.

Any Android-produced signature over these canonical bytes must verify with
`edge_daemon.crypto.verify`; any byte-level deviation (key order,
whitespace, base64 variant) fails closed server-side.

## Layout

```
app/src/main/java/ng/sos/edge/KeystoreSigner.kt   — signer + canonicalization
```

`EDGE_SIGNER=se` on the daemon side selects the PKCS#11 SE backend
(`edge_daemon/se_signer.py`); this module is the equivalent seam for
Android-based POS hardware.
