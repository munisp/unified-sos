"""Constitutional ratification gate for state-police operational modules.

Legal basis (docs/ppp-pipeline/state-police-impact.md):

- The constitutional amendment (Senate 84/109 on 24 June 2026; harmonized
  House bill HB.2797, July 2026) still requires ratification by **at least
  24 of 36 State Houses of Assembly** plus presidential assent.
- Until ratification + assent, the **Ebubeagu precedent** (FHC Abakaliki,
  14 Feb 2023: outfit declared unconstitutional, disbanded, ₦50m damages)
  bars armed state-force build-out.
- No state force operates until NASS-certified against national standards.

Endpoint categories tagged ``ratification_gated`` (arms register, state-force
stand-up) return HTTP 423 Locked while the gate is closed. Community vigilante
CAD (Amotekun, So-Safe, BSCPG, LNSC) and trust-fund administration are
ratification-independent and always available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Legal-basis message returned with HTTP 423 while the gate is closed.
LEGAL_BASIS_MESSAGE = (
    "State-police operational capability is constitutionally gated: the amendment "
    "requires ratification by at least 24 of 36 State Houses of Assembly plus "
    "presidential assent, and NASS certification against national standards, before "
    "any state force operates. Until then the Ebubeagu precedent (FHC Abakaliki, "
    "14 Feb 2023) bars armed state-force enablement. Ratification-independent "
    "modules (community vigilante CAD, trust-fund administration) remain available."
)


@dataclass(frozen=True)
class RatificationGate:
    """Config flag controlling ratification-gated endpoint categories.

    Default FALSE. Production wiring: set from the ratification tally tracker
    only after certified ratification (>= 24 of 36 assemblies) AND presidential
    assent are recorded; never from tenant-level configuration.
    """

    ratified: bool = False
    legal_basis: str = LEGAL_BASIS_MESSAGE

    @classmethod
    def from_env(cls) -> "RatificationGate":
        return cls(ratified=os.environ.get("SOS_POLICE_RATIFIED", "").lower() == "true")

    def check(self) -> None:
        """Raise :class:`GatedError` when the gate is closed."""
        if not self.ratified:
            raise GatedError(self.legal_basis)


class GatedError(Exception):
    """Raised when a ratification-gated capability is invoked pre-ratification."""

    def __init__(self, legal_basis: str) -> None:
        self.legal_basis = legal_basis
        super().__init__(legal_basis)
