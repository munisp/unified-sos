"""WebRTC gateway binding for live CCTV/drone streams (fail-closed seam).

Stream metadata routing for mod-police-cad: each session binds a camera
(CCTV) or drone feed to a WebRTC peer via SDP offer/answer exchange with the
media gateway. Selection idiom mirrors mod-erp-bridge ``build_adapter``:

* Default (``SOS_CAD_PROFILE`` unset / ``fixture``/``local``/``test``) is the
  deterministic :class:`FixtureWebRTCGateway` — no network, canned SDP
  answer, in-memory session tracking.
* ``SOS_CAD_PROFILE=production`` hard-fails AT BOOT with
  :class:`AdapterUnavailableError` unless ``SOS_WEBRTC_GATEWAY_URL`` is set
  AND the optional ``aiortc`` package is importable.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol
from uuid import uuid4


class AdapterUnavailableError(RuntimeError):
    """Raised when a production WebRTC gateway is selected without the
    optional dependency or configuration required to reach it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


STREAM_KINDS = ("cctv", "drone")


@dataclass(frozen=True)
class StreamSession:
    """One established WebRTC stream session (metadata only — media flows
    peer-to-gateway, never through this service)."""

    session_id: str
    camera_id: str
    tenant_state_id: str
    kind: str  # cctv | drone
    started_at: str
    sdp_answer: str


class WebRTCGateway(Protocol):
    """Media-gateway seam. Implementations must be tenant-aware: session
    metadata always carries ``tenant_state_id``."""

    def create_stream_session(
        self, camera_id: str, tenant_state_id: str, kind: str, sdp_offer: str
    ) -> StreamSession: ...

    def list_sessions(self, tenant_state_id: Optional[str] = None) -> List[StreamSession]: ...

    def close_session(self, session_id: str) -> bool: ...


_CANNED_SDP_ANSWER = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=sos-mod-police-cad-fixture\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "a=recvonly\r\n"
    "a=rtpmap:96 H264/90000\r\n"
)


class FixtureWebRTCGateway:
    """Deterministic in-memory gateway for tests and local runs.

    Simulates session establishment: accepts any syntactically non-empty SDP
    offer, returns a canned SDP answer, tracks sessions in memory.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, StreamSession] = {}

    def create_stream_session(
        self, camera_id: str, tenant_state_id: str, kind: str, sdp_offer: str
    ) -> StreamSession:
        if kind not in STREAM_KINDS:
            raise ValueError(f"kind must be one of {STREAM_KINDS}")
        if not sdp_offer or "m=" not in sdp_offer:
            raise ValueError("sdp_offer is not a syntactically valid SDP offer")
        session = StreamSession(
            session_id=f"str-{uuid4().hex[:10]}",
            camera_id=camera_id,
            tenant_state_id=tenant_state_id,
            kind=kind,
            started_at=_now(),
            sdp_answer=_CANNED_SDP_ANSWER,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def list_sessions(self, tenant_state_id: Optional[str] = None) -> List[StreamSession]:
        with self._lock:
            sessions = list(self._sessions.values())
        if tenant_state_id is not None:
            sessions = [s for s in sessions if s.tenant_state_id == tenant_state_id]
        return sessions

    def close_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None


class AiortcWebRTCGateway:
    """Production seam: delegates SDP offer/answer to the media gateway.

    Fail-closed: construction requires the optional ``aiortc`` package AND a
    configured gateway URL; any gateway error at call time raises
    :class:`AdapterUnavailableError` rather than degrading silently.
    """

    def __init__(self, gateway_url: str) -> None:
        if not gateway_url:
            raise AdapterUnavailableError(
                "SOS_WEBRTC_GATEWAY_URL not configured; WebRTC gateway unavailable "
                "(fail-closed)"
            )
        try:
            import aiortc  # type: ignore[import-not-found]  # noqa: F401
        except ImportError as exc:
            raise AdapterUnavailableError(
                "aiortc package not installed; WebRTC gateway unavailable "
                "(fail-closed)"
            ) from exc
        self._gateway_url = gateway_url.rstrip("/")

    def create_stream_session(
        self, camera_id: str, tenant_state_id: str, kind: str, sdp_offer: str
    ) -> StreamSession:
        if kind not in STREAM_KINDS:
            raise ValueError(f"kind must be one of {STREAM_KINDS}")
        try:
            import httpx

            resp = httpx.post(
                f"{self._gateway_url}/sessions",
                json={
                    "camera_id": camera_id,
                    "tenant_state_id": tenant_state_id,
                    "kind": kind,
                    "sdp_offer": sdp_offer,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            body = resp.json()
        except AdapterUnavailableError:
            raise
        except Exception as exc:  # fail closed on any gateway error
            raise AdapterUnavailableError(
                f"WebRTC gateway at {self._gateway_url} unreachable/rejected: {exc}"
            ) from exc
        return StreamSession(
            session_id=body["session_id"],
            camera_id=camera_id,
            tenant_state_id=tenant_state_id,
            kind=kind,
            started_at=_now(),
            sdp_answer=body["sdp_answer"],
        )

    def list_sessions(self, tenant_state_id: Optional[str] = None) -> List[StreamSession]:
        raise AdapterUnavailableError(
            "session listing is served from the CAD domain store; the media "
            "gateway does not enumerate tenant sessions"
        )

    def close_session(self, session_id: str) -> bool:
        try:
            import httpx

            resp = httpx.delete(f"{self._gateway_url}/sessions/{session_id}", timeout=10.0)
            return resp.status_code in (200, 202, 204)
        except Exception as exc:
            raise AdapterUnavailableError(
                f"WebRTC gateway at {self._gateway_url} unreachable: {exc}"
            ) from exc


def build_webrtc_gateway(env: Optional[Dict[str, str]] = None) -> WebRTCGateway:
    """Select the WebRTC gateway from ``SOS_CAD_PROFILE``.

    Default ``fixture``/unset is the deterministic in-memory gateway.
    ``production`` fails closed AT BOOT when ``SOS_WEBRTC_GATEWAY_URL`` is
    unset or ``aiortc`` is not importable.
    """
    env = dict(os.environ if env is None else env)
    profile = env.get("SOS_CAD_PROFILE", "fixture")
    if profile in ("fixture", "local", "test"):
        return FixtureWebRTCGateway()
    if profile in ("production", "live"):
        return AiortcWebRTCGateway(env.get("SOS_WEBRTC_GATEWAY_URL", ""))
    raise AdapterUnavailableError(
        f"unknown SOS_CAD_PROFILE {profile!r}; expected fixture|production"
    )
