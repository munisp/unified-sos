"""Signature verification: device key signs, gateway verifies, tampering fails."""
from edge_daemon.crypto import DeviceSigner, verify
from edge_daemon.daemon import EdgeDaemon
from edge_daemon.models import EWaybillPayload

DEVICE = "POS-OGN-SAGAMU-002"


def test_sign_and_verify_roundtrip(tmp_path):
    signer = DeviceSigner.generate(DEVICE)
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=signer)
    rec = daemon.issue(
        EWaybillPayload(
            state_id="benue",
            waybill_number="WB-BEN-2026-0001",
            consignor_id="FARM-COOP-12",
            consignee_id="MKT-LAG-MILE12",
            produce_type="YAM_TUBERS",
            quantity_kg=8_200.0,
            vehicle_plate="BEN-221-ZX",
            origin="GBOKO",
            destination="LAGOS_MILE12",
            levy_kobo=250_000,
        )
    )
    assert verify(rec.signer_public_key, rec.signing_bytes(), rec.signature)
    daemon.close()


def test_tampered_payload_fails_verification(tmp_path):
    signer = DeviceSigner.generate(DEVICE)
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=signer)
    rec = daemon.issue(
        EWaybillPayload(
            state_id="benue",
            waybill_number="WB-BEN-2026-0002",
            consignor_id="FARM-COOP-12",
            consignee_id="MKT-LAG-MILE12",
            produce_type="RICE_PADDY",
            quantity_kg=1_000.0,
            vehicle_plate="BEN-221-ZX",
            origin="GBOKO",
            destination="LAGOS_MILE12",
            levy_kobo=250_000,
        )
    )
    tampered = rec.model_copy(deep=True)
    tampered.payload.levy_kobo = 1  # attacker edits the levy after issuance
    assert not verify(
        tampered.signer_public_key, tampered.signing_bytes(), tampered.signature
    )
    daemon.close()


def test_wrong_device_key_fails_verification(tmp_path):
    signer = DeviceSigner.generate(DEVICE)
    other = DeviceSigner.generate("POS-OTHER-999")
    daemon = EdgeDaemon(tmp_path / "edge.db", DEVICE, signer=signer)
    rec = daemon.issue(
        EWaybillPayload(
            state_id="benue",
            waybill_number="WB-BEN-2026-0003",
            consignor_id="F",
            consignee_id="C",
            produce_type="MAIZE",
            quantity_kg=100.0,
            vehicle_plate="X",
            origin="A",
            destination="B",
            levy_kobo=100,
        )
    )
    forged = rec.model_copy(update={"signer_public_key": other.public_key_b64()})
    assert not verify(forged.signer_public_key, forged.signing_bytes(), forged.signature)
    daemon.close()


def test_signing_bytes_canonical_order():
    """Signing bytes must be stable regardless of dict construction order."""
    signer = DeviceSigner.generate(DEVICE)
    msg1 = b'{"a":1,"b":2}'
    sig1 = signer.sign(msg1)
    assert verify(signer.public_key_b64(), msg1, sig1)
    assert not verify(signer.public_key_b64(), b'{"a":1,"b":3}', sig1)
