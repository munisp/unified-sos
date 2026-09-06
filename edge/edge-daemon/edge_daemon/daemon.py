"""EdgeDaemon — convenience composition of signer + outbox + sync engine."""
from __future__ import annotations

from pathlib import Path
from typing import Union

from .crypto import DeviceSigner
from .models import Payload, SignedRecord
from .outbox import Outbox
from .sync import SyncEngine


class EdgeDaemon:
    """Reference edge daemon: issue signed records offline, sync on reconnect."""

    def __init__(
        self,
        db_path: Union[str, Path],
        device_id: str,
        signer: DeviceSigner | None = None,
        capacity: int = 10_000,
    ) -> None:
        self.signer = signer or DeviceSigner.generate(device_id)
        self.outbox = Outbox(db_path, device_id, capacity=capacity)

    @classmethod
    def reopen(
        cls, db_path: Union[str, Path], device_id: str, signer: DeviceSigner
    ) -> "EdgeDaemon":
        """Resume after restart: pending records and sequence continue from disk."""
        return cls(db_path, device_id, signer=signer)

    def issue(self, payload: Payload) -> SignedRecord:
        """Sign and buffer a record while offline."""
        return self.outbox.enqueue(self.signer, payload)

    def sync(self, engine: SyncEngine | None = None, **engine_kwargs) -> int:
        """Push all pending records through a sync engine (caller-owned or ad-hoc)."""
        own = engine is None
        engine = engine or SyncEngine(self.outbox, **engine_kwargs)
        try:
            return engine.sync_all()
        finally:
            if own:
                engine.close()

    def close(self) -> None:
        self.outbox.close()
