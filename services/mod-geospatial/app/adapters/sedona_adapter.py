"""Apache Sedona / SedonaDB adapter (analytical engine).

Local/test mode never submits cluster jobs: deterministic local equivalents
(``geospatial/local/*``) are executed by the service layer instead. This
adapter is the production submission seam.

Fail-closed contract
--------------------
* No ``GEOSPATIAL_SEDONA_ENDPOINT`` → ``AdapterUnavailableError`` at
  construction (never silently no-ops).
* Endpoint set but no Sedona/Spark driver importable →
  ``AdapterUnavailableError`` at construction.
* Unknown job type (no spec under ``geospatial/sedona/``) →
  ``AdapterUnavailableError`` at submission.

Job specs under ``geospatial/sedona/`` are the versioned artifacts submitted
to the cluster: ``*.sql`` specs run as Sedona SQL statements; ``*.py`` specs
are shipped to the Spark driver and executed as Sedona/Ray jobs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from ..domain import AdapterUnavailableError

ENDPOINT_ENV = "GEOSPATIAL_SEDONA_ENDPOINT"

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SPEC_DIR = _REPO_ROOT / "geospatial" / "sedona"

#: Mapping from service job types to versioned cluster job specs.
JOB_SPECS: dict[str, str] = {
    "UNASSESSED_PROPERTY_JOIN": "unassessed_property_join.sql",
    "NDVI_CHANGE_DETECTION": "ndvi_change_detection.py",
}


class SedonaAdapter:
    """Submits jobs to a Sedona (Spark) or SedonaDB cluster."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        *,
        spec_dir: Optional[str] = None,
        session: Any = None,
    ) -> None:
        self._endpoint = endpoint or os.environ.get(ENDPOINT_ENV)
        if not self._endpoint:
            raise AdapterUnavailableError(f"{ENDPOINT_ENV} is not set; Sedona adapter fails closed")
        self._spec_dir = Path(spec_dir) if spec_dir else DEFAULT_SPEC_DIR
        # A pre-built session may be injected (tests / embedded deployments);
        # otherwise the Spark/Sedona stack must be importable.
        self._session = session
        if self._session is None and not self._spark_stack_available():
            raise AdapterUnavailableError(
                "Sedona adapter requires the optional 'pyspark' + 'apache-sedona' "
                "packages (or an injected session); fails closed"
            )

    @staticmethod
    def _spark_stack_available() -> bool:
        try:
            import pyspark  # noqa: F401
            import sedona  # noqa: F401

            return True
        except ImportError:
            return False

    def job_spec_path(self, job_type: str) -> Path:
        """Resolve the versioned job spec under ``geospatial/sedona/``."""

        spec_name = JOB_SPECS.get(job_type)
        if spec_name is None:
            raise AdapterUnavailableError(
                f"no Sedona job spec registered for job type {job_type!r}; fails closed"
            )
        path = self._spec_dir / spec_name
        if not path.is_file():
            raise AdapterUnavailableError(f"Sedona job spec missing: {path}; fails closed")
        return path

    def _get_session(self) -> Any:  # pragma: no cover - production path
        if self._session is not None:
            return self._session
        from sedona.spark import SedonaContext

        self._session = (
            SedonaContext.builder()
            .appName("sos-geospatial")
            .master(self._endpoint)
            .getOrCreate()
        )
        return self._session

    def submit_job(self, job_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Submit a cluster job from a versioned spec.

        ``*.sql`` specs are executed as Sedona SQL with the job parameters
        applied as session configuration (``sos.job.param.*``). ``*.py`` specs
        are shipped to the Spark driver via ``SparkContext.addPyFile`` and are
        responsible for their own execution entrypoint.
        """

        spec = self.job_spec_path(job_type)
        session = self._get_session()
        for key, value in parameters.items():
            session.conf.set(f"sos.job.param.{key}", str(value))
        if spec.suffix == ".sql":
            statement = spec.read_text(encoding="utf-8")
            result = session.sql(statement)
            rows = result.collect()
            return {
                "job_type": job_type,
                "spec": str(spec),
                "engine": "sedona-sql",
                "endpoint": self._endpoint,
                "row_count": len(rows),
            }
        session.sparkContext.addPyFile(str(spec))
        return {
            "job_type": job_type,
            "spec": str(spec),
            "engine": "sedona-pyspark",
            "endpoint": self._endpoint,
            "submitted": True,
        }
