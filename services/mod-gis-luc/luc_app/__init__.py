"""mod-gis-luc — Land Use Charge (LUC) valuation & billing engine.

WP-06 / EPIC-06. Consumes the Sedona unassessed-property join output
(geospatial/sedona/unassessed_property_join.sql, published as
``ng.sos.gis.unassessed_property_discovered``) and turns findings into
policy-driven LUC bills per state tenant. Monetary amounts are integer kobo,
consistent with the platform ledger conventions (ledger/chart-of-accounts.md).
"""

__version__ = "1.0.0"
