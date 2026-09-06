package ng.sos.edge

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import java.security.interfaces.EdECPublicKey
import java.security.spec.EdECPoint
import java.security.spec.NamedParameterSpec

/**
 * Device-bound Ed25519 signer backed by Android Keystore / StrongBox.
 *
 * Byte-level interop contract with edge/edge-daemon (edge_daemon/models.py
 * `SignedRecord.signing_bytes` and edge_daemon/crypto.py `verify`):
 *
 *  - signing bytes: canonical JSON of {device_id, sequence, payload} with
 *    lexicographically sorted keys and no insignificant whitespace, UTF-8;
 *  - signature: raw Ed25519 over those bytes, base64url *with* padding
 *    (Python `base64.urlsafe_b64encode` — note Android's Base64.URL_SAFE is
 *    unpadded by default, so WRAP/NO_PADDING flags must not strip '=');
 *  - public key: raw 32-byte Ed25519 key, base64url with padding.
 *
 * Conformance is pinned by
 * edge/edge-daemon/tests/fixtures/android_sig_vectors.json.
 */
class KeystoreSigner(
    private val deviceId: String,
    private val keyAlias: String = "edge-device",
    private val preferStrongBox: Boolean = true,
) {
    private val keyStore: KeyStore =
        KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }

    /** Generate (once) a non-exportable Ed25519 key inside the keystore. */
    fun ensureKey() {
        if (keyStore.containsAlias(keyAlias)) return
        val builder = KeyGenParameterSpec.Builder(
            keyAlias,
            KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY,
        ).apply {
            setAlgorithmParameterSpec(NamedParameterSpec.ED25519)
            // Key material never leaves hardware; no attestation challenge here
            // (device attestation is a separate provisioning flow).
            if (preferStrongBox) setIsStrongBoxBacked(true)
        }
        try {
            KeyPairGenerator.getInstance("Ed25519", ANDROID_KEYSTORE)
                .apply { initialize(builder.build()) }
                .generateKeyPair()
        } catch (e: Exception) {
            if (!preferStrongBox) throw e
            // StrongBox unavailable on this handset: retry with TEE keystore.
            KeystoreSigner(deviceId, keyAlias, preferStrongBox = false).ensureKey()
                .also { keyStore.load(null) }
        }
    }

    /** Sign [message] on-device; returns base64url (padded) signature. */
    fun sign(message: ByteArray): String {
        val entry = keyStore.getEntry(keyAlias, null) as KeyStore.PrivateKeyEntry
        val sig = Signature.getInstance("Ed25519").run {
            initSign(entry.privateKey)
            update(message)
            sign()
        }
        return b64u(sig)
    }

    /** base64url (padded) raw 32-byte Ed25519 public key. */
    fun publicKeyB64(): String {
        val cert = keyStore.getCertificate(keyAlias)
        val pub = cert.publicKey as EdECPublicKey
        return b64u(edPointToRaw(pub.point))
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"

        /**
         * Canonical signing bytes — MUST equal Python
         * `json.dumps({device_id, sequence, payload}, sort_keys=True,
         * separators=(",", ":"))`. JSONObject keys are emitted in sorted
         * order recursively with no whitespace.
         */
        fun canonicalSigningBytes(
            deviceId: String,
            sequence: Long,
            payloadJson: String,
        ): ByteArray {
            val body = JSONObject()
                .put("device_id", deviceId)
                .put("payload", sortedJson(JSONObject(payloadJson)))
                .put("sequence", sequence)
            return sortedJson(body).toString().toByteArray(Charsets.UTF_8)
        }

        /** Deep-sort a JSONObject's keys lexicographically (compact output). */
        private fun sortedJson(obj: JSONObject): JSONObject {
            val out = JSONObject()
            obj.keys().asSequence().sorted().forEach { k ->
                out.put(k, when (val v = obj.get(k)) {
                    is JSONObject -> sortedJson(v)
                    else -> v
                })
            }
            return out
        }

        /** base64url WITH padding — matches Python urlsafe_b64encode. */
        private fun b64u(data: ByteArray): String =
            Base64.encodeToString(data, Base64.URL_SAFE or Base64.NO_WRAP).trim()

        /** Encode an EdECPoint to the raw 32-byte little-endian Ed25519 key. */
        private fun edPointToRaw(point: EdECPoint): ByteArray {
            val y = point.y.toByteArray().let {
                it.reversedArray().copyOf(32) // big-endian -> little-endian, pad to 32
            }
            if (!point.isXOdd) return y
            // Set the x-sign bit (MSB of the final little-endian byte).
            return y.also { y[31] = (y[31].toInt() or 0x80).toByte() }
        }
    }
}
