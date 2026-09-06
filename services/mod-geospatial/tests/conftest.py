"""Shared pytest fixtures for mod-geospatial."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import InMemoryGeospatialRepository
from app.service import GeospatialService


@pytest.fixture()
def service(tmp_path):
    return GeospatialService(InMemoryGeospatialRepository(), output_dir=str(tmp_path))


@pytest.fixture()
def client(service):
    return TestClient(create_app(service))
