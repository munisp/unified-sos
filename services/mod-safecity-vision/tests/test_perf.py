"""Performance-oriented tests: embedding memoization, batch face-match
scoring with cached norms, and retention-sweep efficiency. The retention
purge bound to warrant/DPO ``retention_until`` is unchanged."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.adapters import FixtureFaceEngine
from app.domain import VisionStore

TENANT = "lagos"


def test_fixture_engine_memoizes_embeddings():
    engine = FixtureFaceEngine()
    a = engine.embed("frame://subject-1")
    b = engine.embed("frame://subject-1")
    assert a is b  # same object — derivation runs once


def test_batch_match_matches_sequential_semantics():
    store = VisionStore()
    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    store.enroll_face(TENANT, "subject-1", "frame://subject-1",
                      "warrant-1", retention_until=expiry)
    store.enroll_face(TENANT, "subject-2", "frame://subject-2",
                      "warrant-2", retention_until=expiry)
    probes = ["frame://subject-1", "frame://unrelated", "frame://subject-2"]
    batch = store.match_faces_batch(TENANT, probes, authorization_ref="auth-1")

    store2 = VisionStore()
    store2.enroll_face(TENANT, "subject-1", "frame://subject-1",
                       "warrant-1", retention_until=expiry)
    store2.enroll_face(TENANT, "subject-2", "frame://subject-2",
                       "warrant-2", retention_until=expiry)
    sequential = [store2.match_face(TENANT, p, authorization_ref="auth-1")
                  for p in probes]

    assert [(r.matched, r.subject_ref, r.similarity) for r in batch] == [
        (r.matched, r.subject_ref, r.similarity) for r in sequential
    ]
    # every probe audited individually, exactly like sequential calls
    assert len(store._scope(TENANT).face_audit) == len(probes)
    assert len(store._scope(TENANT).matches) == len(probes)


def test_enrolment_norms_cached_and_purged_with_enrolment():
    store = VisionStore()
    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    enr = store.enroll_face(TENANT, "subject-1", "frame://subject-1",
                            "warrant-1", retention_until=expiry)
    store.match_face(TENANT, "frame://probe")
    scope = store._scope(TENANT)
    assert enr.enrolment_id in scope.embedding_norms  # computed once
    norm = scope.embedding_norms[enr.enrolment_id]
    store.match_face(TENANT, "frame://probe-2")
    assert scope.embedding_norms[enr.enrolment_id] == norm  # reused

    past = datetime.now(timezone.utc) + timedelta(days=31)
    counts = store.sweep_retention(now=past)
    assert counts[TENANT]["enrolments"] == 1  # purge bound to warrant expiry
    assert enr.enrolment_id not in scope.embedding_norms
    assert enr.embedding == []  # biometric purged with the record


def test_sweep_skips_empty_tenants_and_memoizes_timestamps():
    store = VisionStore()
    store.register_camera(TENANT, "cam-1", 6.5, 3.3, "rtsp://cam/1")
    assert store.sweep_retention() == {}
    expiry = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    store.enroll_face(TENANT, "subject-1", "frame://subject-1",
                      "warrant-1", retention_until=expiry)
    counts = store.sweep_retention()
    assert counts[TENANT]["enrolments"] == 1
    assert expiry in store._ts_cache  # parsed once, memoized
