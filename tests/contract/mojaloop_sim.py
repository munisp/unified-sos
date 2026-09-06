"""Mojaloop sim harness notes (contract-level, lightweight).

Purpose
-------
Drive the FSPIOP fulfilment path end-to-end against the deterministic
fixture adapters, without standing up a real Mojaloop hub. This module is
the reference for the compose profile ``payments`` (to be added under
deploy/): services mod-education + mod-mobility-switch wired with
FSPIOP_CALLBACK_SECRET / NIBSS HMAC secret from the local secrets overlay.

Scenario (manual / CI contract job)
-----------------------------------
1. Start services with fixture adapters (default local wiring).
2. Prepare: POST /mobility/v1/escrow {transfer_id, batch_id, amount_kobo}
   → escrow PENDING on the escrow account.
3. Fulfil: PUT /transfers/{id} callback to
   POST /education/v1/webhooks/mojaloop with body
   {transfer_id, invoice_id, amount_kobo, transfer_state: "COMMITTED"}
   and header FSPIOP-Signature = "sha256=" + HMAC_SHA256(secret, raw_body).
   → invoice credited; escrow posted via /mobility/v1/escrow/{id}/fulfil.
4. Abort path: transfer_state "ABORTED" → 409 from education; escrow voided
   via /mobility/v1/escrow/{id}/abort.
5. Reconciliation: FixtureNibssAdapter.settlement_report(date) vs
   NibssEBillsAdapter.reconcile_settlement_sheet(expected, actual) must
   yield zero breaks.

The helpers below let a contract job sign and fulfil deterministically;
see services/mod-mobility-switch/tests/test_scheme_adapters.py for the
executable form of these steps.
"""
from __future__ import annotations

import hashlib
import hmac
import json


def sign_fulfilment(secret: str, payload: dict) -> tuple[bytes, dict[str, str]]:
    """Build a signed FSPIOP fulfilment callback body + headers."""
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/vnd.interoperability.transfers+json;version=1.1",
        "FSPIOP-Signature": signature,
    }


def fulfilment_payload(transfer_id: str, invoice_id: str, amount_kobo: int,
                       state: str = "COMMITTED") -> dict:
    return {
        "transfer_id": transfer_id,
        "invoice_id": invoice_id,
        "amount_kobo": amount_kobo,
        "transfer_state": state,
        "completed_timestamp": "2026-01-01T00:00:00Z",
    }
