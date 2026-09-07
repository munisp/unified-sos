"""Canonical state tenant registry constants.

Mirrors the TigerBeetle 128-bit account layout (``ledger/chart-of-accounts.md``)
and the ``tenant_state_id`` enum in
``contracts/policy-packs/revenue-split.schema.json``.
"""

from __future__ import annotations

#: State tenant IDs (bits 0..15 of the TigerBeetle account identifier).
STATE_TENANT_IDS: dict[str, int] = {
    "lagos": 0x0001,
    "ogun": 0x0002,
    "osun": 0x0003,
    "benue": 0x0004,
    "nasarawa": 0x0005,
    "taraba": 0x0006,
}

#: All 37 state tenant directories under config/states/ (whitelabel branding
#: coverage). Provisioning (`tenant create`) remains restricted to
#: STATE_TENANT_IDS pending contract enum expansion.
ALL_STATE_IDS: tuple[str, ...] = (
    "abia", "adamawa", "akwa_ibom", "anambra", "bauchi", "bayelsa", "benue",
    "borno", "cross_river", "delta", "ebonyi", "edo", "ekiti", "enugu", "fct",
    "gombe", "imo", "jigawa", "kaduna", "kano", "katsina", "kebbi", "kogi",
    "kwara", "lagos", "nasarawa", "niger", "ogun", "ondo", "osun", "oyo",
    "plateau", "rivers", "sokoto", "taraba", "yobe", "zamfara",
)

#: Tenant tiers per the control-plane contract.
TIERS: tuple[str, ...] = ("shared", "hybrid", "dedicated")

#: States treated as agrarian/extractive for concession guardrail ceilings.
AGRARIAN_STATES: frozenset[str] = frozenset({"osun", "benue", "nasarawa", "taraba"})

#: Revenue-share ceilings (%) by state class — procurement guardrails.
REVENUE_SHARE_CEILINGS: dict[str, float] = {
    "lagos": 8.0,
    "ogun": 8.0,
    "osun": 15.0,
    "benue": 15.0,
    "nasarawa": 15.0,
    "taraba": 15.0,
}

#: Default beneficiary → TigerBeetle account class mapping for chart bootstrap.
DEFAULT_CHART_OF_ACCOUNTS: list[dict[str, object]] = [
    {"code": 1001, "name": "Payer Clearing Account", "class": "clearing_settlement"},
    {"code": 2010, "name": "MDA Retention Account", "class": "mda_retention"},
    {"code": 2020, "name": "Local Government Share Pool", "class": "mda_retention"},
    {"code": 2099, "name": "PPP Tech Concessionaire Escrow", "class": "mda_retention"},
    {"code": 3001, "name": "State Consolidated Revenue Fund (TSA)", "class": "state_treasury"},
    {"code": 4001, "name": "Security Trust Fund", "class": "statutory_trust"},
    {"code": 4002, "name": "Transport Union Commission Pool", "class": "statutory_trust"},
    {"code": 5001, "name": "Federal Royalty Pass-Through (segregated)", "class": "federal_passthrough"},
]

#: TigerBeetle transfer codes (ledger/chart-of-accounts.md).
TRANSFER_CODES: dict[int, str] = {
    101: "Land Title Tax / LUC",
    102: "MDA Retention leg",
    103: "Concessionaire Share leg",
    110: "Mineral levy (state-competent)",
    120: "Haulage / WIM penalty",
    130: "Market stallage / micro-levy",
    140: "Transit ticketing",
    150: "Hospital / education consolidated billing",
}
