"""Performance-oriented tests: shared OCR HTTP client reuse, content-hash
index for dedupe, and the async upload path (offloaded to a worker thread)."""

from __future__ import annotations

import base64
import threading

from landdocs_app import adapters
from landdocs_app.adapters import FixtureOcrEngine
from landdocs_app.domain import LandDocsStore

TENANT = {"X-State-Tenant": "osun"}
BASE = "/api/v1/states/osun/land-docs"


def test_shared_http_client_is_module_level_singleton():
    c1 = adapters._shared_http_client()
    c2 = adapters._shared_http_client()
    assert c1 is c2  # one client per process — no per-call construction


def test_dedupe_uses_content_hash_index():
    store = LandDocsStore(ocr_engine=FixtureOcrEngine())
    content = b"affidavit of purchase " * 8
    store.register_document("osun", "Deed of Assignment", "deed.pdf",
                            content, "clerk-1")
    scope = store._scope("osun")
    expected_hash = next(iter(scope.hash_index))
    assert scope.hash_index[expected_hash]  # indexed at registration
    # exact-content duplicate detected via the O(1) index
    warnings = store.find_duplicates("osun", "Unrelated Title", expected_hash)
    assert [w.reason for w in warnings] == ["identical_content_hash"]
    assert warnings[0].score == 1.0


def test_fuzzy_dedupe_results_unchanged_with_quick_ratio_pruning():
    store = LandDocsStore(ocr_engine=FixtureOcrEngine())
    store.register_document("osun", "Deed of Assignment Plot 42", "a.pdf",
                            b"content-alpha-pdf-bytes", "clerk-1")
    warnings = store.find_duplicates("osun", "Deed of Assignment Plot 43",
                                     "0" * 64)
    assert len(warnings) == 1 and warnings[0].reason == "similar_title"


def test_register_upload_path_is_async_and_offloaded(client):
    ran_in_thread: list[str] = []
    orig_put = None

    from landdocs_app.adapters import FixtureObjectStore
    orig_put = FixtureObjectStore.put

    def spy_put(self, key, content):
        ran_in_thread.append(threading.current_thread().name)
        return orig_put(self, key, content)

    FixtureObjectStore.put = spy_put
    try:
        resp = client.post(f"{BASE}/documents", headers=TENANT, json={
            "title": "Survey Plan", "filename": "survey.pdf",
            "content_base64": base64.b64encode(b"survey bytes payload").decode(),
            "registered_by": "clerk-9",
        })
    finally:
        FixtureObjectStore.put = orig_put
    assert resp.status_code == 201, resp.text
    # blocking work ran in anyio's worker threadpool, not the event loop
    assert ran_in_thread and "anyio" in ran_in_thread[0].lower()
