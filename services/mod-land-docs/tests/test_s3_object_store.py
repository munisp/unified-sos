"""S3ObjectStore: boto3 wiring (fake client), fail-closed, fixture default."""
from __future__ import annotations

import sys

import pytest

from landdocs_app.adapters import (
    AdapterUnavailableError,
    FixtureObjectStore,
    S3ObjectStore,
    object_store_from_env,
)


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3Client:
    """In-memory stand-in for a boto3 S3 client (no network)."""

    def __init__(self):
        self.objects = {}
        self.calls = []

    def put_object(self, Bucket, Key, Body):
        self.calls.append(("put", Bucket, Key))
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Bucket, Key))
        try:
            return {"Body": _FakeBody(self.objects[(Bucket, Key)])}
        except KeyError:
            raise RuntimeError("NoSuchKey")


class TestS3ObjectStore:
    def test_put_get_round_trip_with_fake_client(self):
        fake = _FakeS3Client()
        store = S3ObjectStore(
            environ={"SOS_LANDDOCS_BUCKET": "land-docs"}, client=fake
        )
        ref = store.put("deeds/deed-1.pdf", b"%PDF-bytes")
        assert ref == "s3://land-docs/deeds/deed-1.pdf"
        assert store.get(ref) == b"%PDF-bytes"
        assert fake.calls == [
            ("put", "land-docs", "deeds/deed-1.pdf"),
            ("get", "land-docs", "deeds/deed-1.pdf"),
        ]

    def test_get_missing_object_raises_keyerror(self):
        store = S3ObjectStore(
            environ={"SOS_LANDDOCS_BUCKET": "land-docs"}, client=_FakeS3Client()
        )
        with pytest.raises(KeyError):
            store.get("s3://land-docs/nope.pdf")

    def test_get_rejects_non_s3_ref(self):
        store = S3ObjectStore(
            environ={"SOS_LANDDOCS_BUCKET": "land-docs"}, client=_FakeS3Client()
        )
        with pytest.raises(KeyError):
            store.get("fixture://deeds/deed-1.pdf")

    def test_bucket_required_fails_closed(self):
        with pytest.raises(AdapterUnavailableError):
            S3ObjectStore(environ={})

    def test_boto3_missing_fails_closed_when_bucket_set(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_boto3(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("no boto3 in sandbox")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_boto3)
        monkeypatch.delitem(sys.modules, "boto3", raising=False)
        store = S3ObjectStore(environ={"SOS_LANDDOCS_BUCKET": "land-docs"})
        with pytest.raises(AdapterUnavailableError):
            store.put("k", b"v")

    def test_endpoint_and_keys_wired_into_boto3_client(self, monkeypatch):
        captured = {}

        class _FakeBoto3:
            @staticmethod
            def client(service, **kwargs):
                captured["service"] = service
                captured["kwargs"] = kwargs
                return _FakeS3Client()

        monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3())
        store = S3ObjectStore(
            environ={
                "SOS_LANDDOCS_BUCKET": "land-docs",
                "SOS_LANDDOCS_S3_ENDPOINT": "http://minio:9000",
                "SOS_LANDDOCS_S3_ACCESS_KEY": "ak",
                "SOS_LANDDOCS_S3_SECRET_KEY": "sk",
            }
        )
        store.put("k", b"v")
        assert captured["service"] == "s3"
        assert captured["kwargs"] == {
            "endpoint_url": "http://minio:9000",
            "aws_access_key_id": "ak",
            "aws_secret_access_key": "sk",
        }

    def test_fixture_default_unchanged_when_bucket_unset(self):
        store = object_store_from_env({})
        assert isinstance(store, FixtureObjectStore)
        ref = store.put("k", b"v")
        assert ref == "fixture://k"
        assert store.get(ref) == b"v"

    def test_env_bucket_selects_s3_store(self):
        store = object_store_from_env(
            {"SOS_LANDDOCS_BUCKET": "land-docs"}
        )
        assert isinstance(store, S3ObjectStore)
        assert store.bucket == "land-docs"
