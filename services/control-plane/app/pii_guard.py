"""PII guard — request middleware enforcing the control-plane data boundary.

The control plane holds ZERO citizen PII (docs/architecture/01-architecture-
blueprint.md). This middleware scans JSON request bodies for PII-looking field
names (at any nesting depth) and rejects with HTTP 400 before routing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

#: Field-name fragments that indicate citizen PII. Matched case-insensitively
#: against normalized keys (separators removed), so `firstName`, `first_name`
#: and `first-name` all match.
PII_FIELD_PATTERNS: tuple[str, ...] = (
    "nin", "bvn", "passport", "biometric", "fingerprint",
    "firstname", "lastname", "middlename", "fullname",
    "dateofbirth", "dob", "phonenumber", "msisdn", "emailaddress",
    "homeaddress", "residentialaddress", "streetaddress",
    "mothersmaiden", "nextofkin", "nationalid", "voterscard", "driverslicense",
)

#: 11-digit NIN / BVN value pattern.
NIN_BVN_VALUE = re.compile(r"^\d{11}$")

#: MSISDN-ish value pattern (Nigerian +234 / 0-prefix mobile).
PHONE_VALUE = re.compile(r"^(\+?234|0)\d{10}$")


def _find_pii(node: Any, path: str = "$") -> list[str]:
    """Return dotted paths of PII-looking fields/values in a JSON document."""
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
            child = f"{path}.{key}"
            if any(p in norm for p in PII_FIELD_PATTERNS):
                hits.append(child)
            hits.extend(_find_pii(value, child))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            hits.extend(_find_pii(item, f"{path}[{i}]"))
    elif isinstance(node, str):
        if NIN_BVN_VALUE.match(node) or PHONE_VALUE.match(node):
            hits.append(path)
    return hits


class PiiGuardMiddleware(BaseHTTPMiddleware):
    """Reject JSON bodies containing PII-looking fields with HTTP 400."""

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH") and request.headers.get(
            "content-type", ""
        ).startswith("application/json"):
            body = await request.body()
            if body:
                try:
                    doc = json.loads(body)
                except json.JSONDecodeError:
                    doc = None
                if doc is not None:
                    hits = _find_pii(doc)
                    if hits:
                        return JSONResponse(
                            status_code=400,
                            content={
                                "detail": (
                                    "Control plane data boundary violation: request contains "
                                    "PII-looking fields "
                                    f"({', '.join(hits[:5])}). The control plane stores metadata "
                                    "only — zero citizen PII "
                                    "(docs/architecture/01-architecture-blueprint.md)."
                                )
                            },
                        )
        return await call_next(request)
