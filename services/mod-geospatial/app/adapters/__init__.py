"""Adapter seams for mod-geospatial.

Every adapter has a deterministic local implementation (default in local/test
mode) and an optional production backend that fails closed
(``AdapterUnavailableError``) when its package, endpoint, or credentials are
unavailable.
"""
