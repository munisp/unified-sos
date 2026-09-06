"""Compatibility shim — the event bus now lives in the shared library.

The :class:`EventBus` protocol and its implementations were promoted to
``services/_shared/eventbus`` (P1 Workstream 3: Kafka/Fluvio event backbone +
schema registry) so every service shares one bus implementation. This module
re-exports the shared symbols so existing ``from app.bus import ...`` imports
keep working.

- :class:`InMemoryEventBus` remains the default for local dev and tests.
- :class:`KafkaEventBus` requires the optional ``aiokafka`` package and
  ``EVENT_KAFKA_BOOTSTRAP`` (fail-closed).
- :class:`FluvioEdgeBus` is the edge-tier seam (optional ``fluvio`` package).
"""
from __future__ import annotations

import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[2] / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from eventbus import (  # noqa: E402,F401
    AdapterUnavailableError,
    EventBus,
    EventBusConfigError,
    FluvioEdgeBus,
    InMemoryEventBus,
    KafkaEventBus,
    event_bus_from_env,
    load_topic_map,
    topic_for_channel,
)

__all__ = [
    "AdapterUnavailableError",
    "EventBus",
    "EventBusConfigError",
    "FluvioEdgeBus",
    "InMemoryEventBus",
    "KafkaEventBus",
    "event_bus_from_env",
    "load_topic_map",
    "topic_for_channel",
]
