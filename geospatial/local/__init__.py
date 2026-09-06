"""Local verification runners for the Sedona production jobs (ADR-004).

The canonical implementations remain the Apache Sedona SQL/PySpark jobs in
``geospatial/sedona/``. This package re-executes the *same business logic* on
small GeoJSON/NumPy fixtures with shapely/numpy so CI can verify classification
correctness without a Spark/Sedona cluster or raster infrastructure.
"""
