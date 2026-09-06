"""SOS lakehouse — medallion pipeline reference (WP-15 / EPIC-17/18).

Bronze → Silver → Gold transforms as pure functions over local parquet/JSON
fixtures. Production mapping (see README): Delta Lake on MinIO, Flink
real-time windowing, Ray distributed ML.
"""
