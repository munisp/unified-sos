"""Citizen channel adapters (USSD / IVR) for mod-citizen-portal.

Privacy rule: adapters accept only hashed MSISDNs; raw phone numbers are
hashed at the API edge (app/main.py) before touching the session store.
"""
from .base import AdapterUnavailableError, ChannelResponse, CitizenChannelAdapter

__all__ = ["AdapterUnavailableError", "ChannelResponse", "CitizenChannelAdapter"]
