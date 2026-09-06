"""Apache Sedona / SedonaDB adapter seam (analytical engine).

Local/test mode never submits cluster jobs: deterministic local equivalents
(``geospatial/local/*``) are executed by the service layer instead. This
adapter is the production submission seam and fails closed unless a Sedona
endpoint is explicitly configured.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..domain import AdapterUnavailableError

ENDPOINT_ENV = "GEOSPATIAL_SEDONA_ENDPOINT"


class SedonaAdapter:
    """Production seam for submitting jobs to a Sedona/SedonaDB cluster."""

    def __init__(self, endpoint: Optional[str] = None) -> None:
        self._endpoint = endpoint or os.environ.get(ENDPOINT_ENV)
        if not self._endpoint:
            raise AdapterUnavailableError(f"{ENDPOINT_ENV} is not set; Sedona adapter fails closed")

    def submit_job(self, job_type: str, parameters: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        """Submit a cluster job. Wire-up of the actual Spark/Sedona session is
        an environment-specific deployment concern; the seam guarantees we
        never silently no-op in production."""
        raise AdapterUnavailableError(
            "Sedona cluster submission is not implemented in this build; "
            "run jobs via deterministic local execution instead"
        )
