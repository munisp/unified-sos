"""Scheme adapters: Mojaloop FSPIOP + NIBSS e-Bills (fail-closed idiom)."""

from .base import (
    AdapterUnavailableError,
    RequestContext,
    RetryPolicy,
    SchemeSignatureError,
    new_request_id,
    propagate_request_id,
)
from .fspiop import FspiopAdapter, TransferFulfilment
from .fixtures import FixtureFspiopAdapter, FixtureNibssAdapter
from .nibss_ebills import (
    NibssEBillsAdapter,
    ReconciliationBreak,
    SettlementRow,
)

__all__ = [
    "AdapterUnavailableError",
    "RequestContext",
    "RetryPolicy",
    "SchemeSignatureError",
    "new_request_id",
    "propagate_request_id",
    "FspiopAdapter",
    "TransferFulfilment",
    "FixtureFspiopAdapter",
    "FixtureNibssAdapter",
    "NibssEBillsAdapter",
    "ReconciliationBreak",
    "SettlementRow",
]
