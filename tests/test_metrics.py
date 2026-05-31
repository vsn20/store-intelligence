# PROMPT: Write pytest tests for a FastAPI retail analytics API with endpoints:
# POST /events/ingest, GET /stores/{id}/metrics, GET /stores/{id}/metrics?date=,
# GET /stores/{id}/funnel, GET /stores/{id}/anomalies, GET /health.
# Use TestClient with SQLite override via conftest. Test: basic ingest, idempotency,
# partial failure, empty store, all-staff, zero purchases, billing metrics,
# reentry dedup in funnel, health stale feed, anomaly queue spike.
# CHANGES MADE: Removed inline DB setup (moved to conftest.py), added ?date= param
# to all metric queries, fixed visitor_id format to match VIS_ prefix,
# fixed metadata structure to include all required fields, adjusted assertion
# for idempotency to check DB count not response count, added zone_id to
# BILLING_QUEUE_JOIN events (required by schema validator).

import uuid
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db

client = TestClient(app)

TODAY_STR = str(datetime.now(timezone.utc).date())
STORE = "ST_TEST"


def make_event(event_type, visitor_id=None, zone_id=None, is_staff=False,
               dwell_ms=0, camera_id="CAM 1", metadata=None, hours_ago=1,
               store_id=None):
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    # zone_id rules: required for zone/billing events, null for ENTRY/EXIT
    if event_type in ("ENTRY", "EXIT", "REENTRY") and zone_id is None:
        zone_id = None
    elif event_type in ("ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL") and zone_id is None:
        zone_id = "SKINCARE"
    elif event_type in ("BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON") and zone_id is None:
        zone_id = "BILLING"

    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id or STORE,
        "camera_id":  camera_id,
        "visitor_id": visitor_id or ("VIS_" + uuid.uuid4().hex[:6]),
        "event_type": event_type,
        "timestamp":  ts.isoformat(),
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   is_staff,
        "confidence": 0.85,
        "metadata":   metadata or {
            "queue_depth": None,
            "sku_zone":    None,
            "session_seq": 1
        }
    }


def ingest(events):
    return client.post("/events/ingest", json={"events": events})


# ── Ingest tests ──────────────────────────────────────────────────────────────

def test_ingest_basic():
    events = [make_event("ENTRY") for _ in range(5)]
    resp = ingest(events)
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 5
    assert data["rejected"] == 0
    assert data["errors"] == []


def test_ingest_idempotent():
    """Calling ingest twice with identical payload must not duplicate DB rows."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import EventORM

    events = [make_event("ENTRY") for _ in range(3)]
    resp1 = ingest(events)
    resp2 = ingest(events)
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Check via a fresh DB query through the override
    db_gen = app.dependency_overrides[get_db]()
    db = next(db_gen)
    count = db.query(EventORM).filter(EventORM.store_id == STORE).count()
    try:
        next(db_gen)
    except StopIteration:
        pass
    assert count == 3  # must be 3, not 6


def test_ingest_partial_failure():
    """Valid + invalid events — valid ones stored, invalid ones reported."""
    valid   = make_event("ENTRY")
    # Missing required fields = invalid (no event_id, no store_id)
    invalid = {
        "event_id":   "not-a-valid-uuid-but-string-is-ok",
        "store_id":   STORE,
        "camera_id":  "CAM 1",
        "visitor_id": "VIS_bad001",
        "event_type": "INVALID_TYPE",   # not in EventType enum → rejected
        "timestamp":  "2026-01-01T00:00:00Z",
        "zone_id":    None,
        "dwell_ms":   0,
        "is_staff":   False,
        "confidence": 0.85,
        "metadata":   {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }
    resp = client.post("/events/ingest", json={"events": [valid, invalid]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] == 1
    assert data["rejected"] == 1
    assert len(data["errors"]) == 1


def test_ingest_batch_limit():
    """Batch of exactly 500 events must be accepted."""
    events = [make_event("ENTRY") for _ in range(500)]
    resp = ingest(events)
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 500


# ── Metrics tests ─────────────────────────────────────────────────────────────

def test_metrics_empty_store():
    """Store with no events returns zeros — must not crash or return null."""
    resp = client.get(f"/stores/EMPTY_STORE/metrics?date={TODAY_STR}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 0
    assert data["conversion_rate"] == 0.0
    assert data["abandonment_rate"] == 0.0
    assert data["current_queue_depth"] == 0
    assert isinstance(data["avg_dwell_per_zone"], dict)


def test_metrics_all_staff():
    """All events flagged is_staff=True → unique_visitors must be 0."""
    events = [make_event("ENTRY", is_staff=True) for _ in range(5)]
    ingest(events)
    resp = client.get(f"/stores/{STORE}/metrics?date={TODAY_STR}")
    assert resp.status_code == 200
    assert resp.json()["unique_visitors"] == 0


def test_metrics_zero_purchases():
    """Visitors present but no billing → conversion_rate = 0.0."""
    events = [make_event("ENTRY") for _ in range(5)]
    ingest(events)
    resp = client.get(f"/stores/{STORE}/metrics?date={TODAY_STR}")
    assert resp.status_code == 200
    assert resp.json()["conversion_rate"] == 0.0


def test_metrics_conversion_with_billing():
    """Entry + billing queue joins → conversion_rate > 0."""
    entries = [make_event("ENTRY", visitor_id=f"VIS_{i:06x}") for i in range(10)]
    billing = [
        make_event("BILLING_QUEUE_JOIN", visitor_id=f"VIS_{i:06x}",
                   zone_id="BILLING", hours_ago=0,
                   metadata={"queue_depth": 2, "sku_zone": "billing", "session_seq": 2})
        for i in range(3)
    ]
    ingest(entries + billing)
    resp = client.get(f"/stores/{STORE}/metrics?date={TODAY_STR}")
    assert resp.status_code == 200
    assert resp.json()["conversion_rate"] > 0


def test_metrics_abandonment_rate():
    """If all billing visitors abandon, abandonment_rate = 1.0."""
    joins = [
        make_event("BILLING_QUEUE_JOIN", visitor_id=f"VIS_j{i:05x}",
                   zone_id="BILLING",
                   metadata={"queue_depth": 1, "sku_zone": "billing", "session_seq": 1})
        for i in range(4)
    ]
    abandons = [
        make_event("BILLING_QUEUE_ABANDON", visitor_id=f"VIS_j{i:05x}",
                   zone_id="BILLING",
                   metadata={"queue_depth": 1, "sku_zone": "billing", "session_seq": 2})
        for i in range(4)
    ]
    ingest(joins + abandons)
    resp = client.get(f"/stores/{STORE}/metrics?date={TODAY_STR}")
    assert resp.status_code == 200
    assert resp.json()["abandonment_rate"] == 1.0


# ── Funnel tests ──────────────────────────────────────────────────────────────

def test_funnel_empty_store():
    resp = client.get(f"/stores/EMPTY_STORE/funnel?date={TODAY_STR}")
    assert resp.status_code == 200
    funnel = resp.json()["funnel"]
    assert funnel[0]["visitors"] == 0
    assert funnel[0]["dropoff_pct"] == 0.0


def test_funnel_reentry_not_double_counted():
    """EXIT + REENTRY for same visitor_id must count as 1 unique entrant."""
    vid = "VIS_retest1"
    events = [
        make_event("ENTRY",   visitor_id=vid, hours_ago=3),
        make_event("EXIT",    visitor_id=vid, hours_ago=2),
        make_event("REENTRY", visitor_id=vid, hours_ago=1),
    ]
    ingest(events)
    resp = client.get(f"/stores/{STORE}/funnel?date={TODAY_STR}")
    assert resp.status_code == 200
    entry_stage = resp.json()["funnel"][0]
    assert entry_stage["visitors"] == 1  # not 2


def test_funnel_dropoff_logic():
    """Entry→Zone→Billing→Purchase funnel dropoffs must be non-negative."""
    events = (
        [make_event("ENTRY",            visitor_id=f"VIS_f{i:05x}") for i in range(10)] +
        [make_event("ZONE_ENTER",       visitor_id=f"VIS_f{i:05x}", zone_id="SKINCARE") for i in range(7)] +
        [make_event("BILLING_QUEUE_JOIN", visitor_id=f"VIS_f{i:05x}", zone_id="BILLING",
                    metadata={"queue_depth": 1, "sku_zone": "billing", "session_seq": 3})
         for i in range(3)]
    )
    ingest(events)
    resp = client.get(f"/stores/{STORE}/funnel?date={TODAY_STR}")
    assert resp.status_code == 200
    funnel = resp.json()["funnel"]
    for stage in funnel:
        assert stage["dropoff_pct"] >= 0
    # Each stage must be <= previous stage
    counts = [s["visitors"] for s in funnel]
    for i in range(1, len(counts)):
        assert counts[i] <= counts[i-1]


# ── Health tests ──────────────────────────────────────────────────────────────

def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "stale_feeds" in data
    assert "last_event_per_store" in data
    assert "checked_at" in data


def test_health_stale_feed():
    """Store whose last event is >10 min ago must appear in stale_feeds."""
    old_event = make_event("ENTRY", hours_ago=1, store_id="STORE_STALE")
    ingest([old_event])
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "STORE_STALE" in resp.json()["stale_feeds"]


def test_health_fresh_feed_not_stale():
    """Store with event just now must NOT appear in stale_feeds."""
    fresh_event = make_event("ENTRY", hours_ago=0, store_id="STORE_FRESH")
    ingest([fresh_event])
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "STORE_FRESH" not in resp.json()["stale_feeds"]


# ── Anomaly tests ─────────────────────────────────────────────────────────────

def test_anomalies_empty_store():
    """Anomaly endpoint on empty store must not crash."""
    resp = client.get("/stores/EMPTY_STORE/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert "anomalies" in data
    assert isinstance(data["anomalies"], list)


def test_anomalies_queue_spike():
    """Queue depth > 5 triggers BILLING_QUEUE_SPIKE with CRITICAL severity."""
    event = make_event(
        "BILLING_QUEUE_JOIN", zone_id="BILLING", hours_ago=0,
        metadata={"queue_depth": 8, "sku_zone": "billing", "session_seq": 1}
    )
    ingest([event])
    resp = client.get(f"/stores/{STORE}/anomalies")
    assert resp.status_code == 200
    anomalies = resp.json()["anomalies"]
    spikes = [a for a in anomalies if a["type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spikes) >= 1
    assert spikes[0]["severity"] == "CRITICAL"
    assert "suggested_action" in spikes[0]