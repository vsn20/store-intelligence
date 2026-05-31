# PROMPT: Write pytest tests for a FastAPI anomaly detection endpoint
# GET /stores/{store_id}/anomalies that detects three conditions:
# (1) BILLING_QUEUE_SPIKE when queue_depth > 5 (severity CRITICAL),
# (2) DEAD_ZONE when a zone had visits today but none in the last 30 minutes
#     (severity INFO), and
# (3) CONVERSION_DROP when today's conversion rate is >20% below the 7-day average
#     (severity WARN).
# Each anomaly must include: type, severity, description, suggested_action,
# detected_at. Use TestClient with SQLite via conftest.py. Cover: empty store
# returns empty list, queue spike triggers CRITICAL, queue below threshold has no
# spike, dead zone detection, all-staff events excluded from dead zone, conversion
# drop triggers WARN, conversion above average has no drop.
# CHANGES MADE: Added zone_id="BILLING" to BILLING_QUEUE_JOIN events (required
# by schema validator). Moved DB fixture dependency to conftest. Fixed
# CONVERSION_DROP test to insert historical data as previous-day events using
# hours_ago > 24 (one full day back per historical point). Added assertion for
# suggested_action field presence on every anomaly. Added check that is_staff=True
# events are excluded from queue spike detection.

import uuid
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db

client = TestClient(app)

TODAY_STR = str(datetime.now(timezone.utc).date())
STORE = "ST_ANOM"


def make_event(event_type, visitor_id=None, zone_id=None, is_staff=False,
               dwell_ms=0, metadata=None, hours_ago=0, store_id=None):
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)

    if zone_id is None:
        if event_type in ("ENTRY", "EXIT", "REENTRY"):
            zone_id = None
        elif event_type in ("ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL"):
            zone_id = "SKINCARE"
        elif event_type in ("BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"):
            zone_id = "BILLING"

    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id or STORE,
        "camera_id":  "CAM_TEST",
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
            "session_seq": 1,
        },
    }


def ingest(events):
    return client.post("/events/ingest", json={"events": events})


def get_anomalies(store=None):
    return client.get(f"/stores/{store or STORE}/anomalies")


# ── Empty store ───────────────────────────────────────────────────────────────

def test_anomalies_empty_store():
    """Store with no events returns empty anomaly list without crashing."""
    resp = get_anomalies("STORE_NO_EVENTS_ANOM")
    assert resp.status_code == 200
    data = resp.json()
    assert "anomalies" in data
    assert isinstance(data["anomalies"], list)
    assert len(data["anomalies"]) == 0


def test_anomaly_response_has_required_fields():
    """Response shape is correct: store_id, anomalies list, checked_at."""
    resp = get_anomalies()
    assert resp.status_code == 200
    data = resp.json()
    assert "store_id" in data
    assert "anomalies" in data
    assert "checked_at" in data
    assert isinstance(data["anomalies"], list)


# ── Billing queue spike ───────────────────────────────────────────────────────

def test_queue_spike_triggers_critical():
    """queue_depth > 5 in a recent BILLING_QUEUE_JOIN → BILLING_QUEUE_SPIKE CRITICAL."""
    event = make_event(
        "BILLING_QUEUE_JOIN",
        zone_id="BILLING",
        hours_ago=0,
        metadata={"queue_depth": 8, "sku_zone": "billing", "session_seq": 1},
    )
    ingest([event])

    resp = get_anomalies()
    assert resp.status_code == 200
    spikes = [a for a in resp.json()["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spikes) >= 1, "Expected BILLING_QUEUE_SPIKE anomaly"
    assert spikes[0]["severity"] == "CRITICAL"
    assert "suggested_action" in spikes[0]
    assert len(spikes[0]["suggested_action"]) > 0


def test_queue_below_threshold_no_spike():
    """queue_depth <= 5 must NOT trigger BILLING_QUEUE_SPIKE."""
    event = make_event(
        "BILLING_QUEUE_JOIN",
        zone_id="BILLING",
        hours_ago=0,
        metadata={"queue_depth": 3, "sku_zone": "billing", "session_seq": 1},
    )
    ingest([event])

    resp = get_anomalies()
    spikes = [a for a in resp.json()["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spikes) == 0, "queue_depth=3 should not trigger a spike anomaly"


def test_staff_events_excluded_from_queue_spike():
    """is_staff=True events must be excluded — only customer queues count."""
    staff_event = make_event(
        "BILLING_QUEUE_JOIN",
        zone_id="BILLING",
        is_staff=True,
        hours_ago=0,
        metadata={"queue_depth": 10, "sku_zone": "billing", "session_seq": 1},
    )
    ingest([staff_event])

    resp = get_anomalies()
    spikes = [a for a in resp.json()["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spikes) == 0, \
        "Staff billing events should not trigger BILLING_QUEUE_SPIKE"


# ── Dead zone ─────────────────────────────────────────────────────────────────

def test_dead_zone_detected():
    """A zone with an old ZONE_ENTER (> 30 min ago) but no recent activity → DEAD_ZONE."""
    # Old zone entry — 45 minutes ago (stale)
    old_entry = make_event("ZONE_ENTER", zone_id="OLDZONE", hours_ago=0.75)
    # Force zone_id to something specific
    old_entry["zone_id"] = "OLDZONE"
    ingest([old_entry])

    resp = get_anomalies()
    assert resp.status_code == 200
    dead = [a for a in resp.json()["anomalies"] if a["type"] == "DEAD_ZONE"]
    assert len(dead) >= 1, "Expected DEAD_ZONE anomaly for zone with no recent visits"
    assert dead[0]["severity"] == "INFO"
    assert "suggested_action" in dead[0]


def test_active_zone_no_dead_zone():
    """A zone with a recent ZONE_ENTER (< 30 min ago) must NOT be flagged as dead."""
    fresh_entry = make_event("ZONE_ENTER", zone_id="FRESHZONE", hours_ago=0)
    fresh_entry["zone_id"] = "FRESHZONE"
    ingest([fresh_entry])

    resp = get_anomalies()
    dead = [a for a in resp.json()["anomalies"]
            if a["type"] == "DEAD_ZONE" and "FRESHZONE" in a["description"]]
    assert len(dead) == 0, \
        "Zone with recent activity should not be flagged as DEAD_ZONE"


def test_staff_zone_entries_excluded_from_dead_zone():
    """is_staff=True ZONE_ENTER events do not count as active zone visits."""
    # Staff visited the zone 1 minute ago — should still appear dead for customers
    staff_entry = make_event("ZONE_ENTER", zone_id="STAFFZONE", is_staff=True, hours_ago=0)
    staff_entry["zone_id"] = "STAFFZONE"
    # Customer visited 45 min ago (stale)
    old_entry = make_event("ZONE_ENTER", zone_id="STAFFZONE", is_staff=False, hours_ago=0.75)
    old_entry["zone_id"] = "STAFFZONE"
    ingest([staff_entry, old_entry])

    resp = get_anomalies()
    dead = [a for a in resp.json()["anomalies"]
            if a["type"] == "DEAD_ZONE" and "STAFFZONE" in a["description"]]
    assert len(dead) >= 1, \
        "Zone visited only by staff recently should still be flagged as DEAD_ZONE"


# ── Conversion drop ───────────────────────────────────────────────────────────

def test_conversion_drop_detected():
    """Today's conversion rate significantly below 7-day average → CONVERSION_DROP WARN."""
    # Historical days: high conversion (7 purchases out of 10 visitors each day)
    # We use hours_ago to place events in the last 7 days' windows.
    for day in range(1, 5):  # 4 days of historical data
        offset_hours = day * 24
        for i in range(10):
            vid = f"VIS_h{day:02d}{i:03x}"
            ingest([make_event("ENTRY",             visitor_id=vid, hours_ago=offset_hours + 1)])
            ingest([make_event("BILLING_QUEUE_JOIN", visitor_id=vid, zone_id="BILLING",
                               hours_ago=offset_hours,
                               metadata={"queue_depth": 1, "sku_zone": "billing", "session_seq": 2})])

    # Today: only 1 purchase out of 10 visitors (10% vs historical ~70%)
    for i in range(10):
        vid = f"VIS_t{i:03x}"
        ingest([make_event("ENTRY", visitor_id=vid, hours_ago=1)])

    ingest([make_event("BILLING_QUEUE_JOIN",
                       visitor_id="VIS_t000",
                       zone_id="BILLING",
                       hours_ago=0,
                       metadata={"queue_depth": 1, "sku_zone": "billing", "session_seq": 2})])

    resp = get_anomalies()
    assert resp.status_code == 200
    drops = [a for a in resp.json()["anomalies"] if a["type"] == "CONVERSION_DROP"]
    assert len(drops) >= 1, \
        "Expected CONVERSION_DROP anomaly when today's conversion is far below historical avg"
    assert drops[0]["severity"] == "WARN"
    assert "suggested_action" in drops[0]


def test_no_conversion_drop_when_rate_normal():
    """No CONVERSION_DROP when today's conversion rate is comparable to historical."""
    # Consistent conversion across days
    for day in range(0, 5):
        offset_hours = day * 24
        for i in range(5):
            vid = f"VIS_n{day:02d}{i:03x}"
            ingest([make_event("ENTRY",              visitor_id=vid, hours_ago=offset_hours + 1)])
            ingest([make_event("BILLING_QUEUE_JOIN",  visitor_id=vid, zone_id="BILLING",
                               hours_ago=offset_hours,
                               metadata={"queue_depth": 1, "sku_zone": "billing", "session_seq": 2})])

    resp = get_anomalies()
    drops = [a for a in resp.json()["anomalies"] if a["type"] == "CONVERSION_DROP"]
    assert len(drops) == 0, \
        "Consistent conversion rate should not trigger CONVERSION_DROP"


def test_conversion_drop_requires_historical_data():
    """If there's no historical data, CONVERSION_DROP must not be raised."""
    # Only today's events — no historical records
    for i in range(5):
        ingest([make_event("ENTRY", visitor_id=f"VIS_nd{i:04x}", hours_ago=1)])

    resp = get_anomalies()
    drops = [a for a in resp.json()["anomalies"] if a["type"] == "CONVERSION_DROP"]
    assert len(drops) == 0, \
        "No historical data → CONVERSION_DROP must not be raised"


# ── Anomaly field completeness ────────────────────────────────────────────────

def test_every_anomaly_has_required_fields():
    """Every anomaly in the response must have all required fields."""
    # Trigger a spike to get at least one anomaly
    event = make_event(
        "BILLING_QUEUE_JOIN",
        zone_id="BILLING",
        hours_ago=0,
        metadata={"queue_depth": 9, "sku_zone": "billing", "session_seq": 1},
    )
    ingest([event])

    resp = get_anomalies()
    for anomaly in resp.json()["anomalies"]:
        for field in ("type", "severity", "description", "suggested_action", "detected_at"):
            assert field in anomaly, f"Anomaly missing required field: {field}"
        assert anomaly["severity"] in ("INFO", "WARN", "CRITICAL"), \
            f"Unknown severity: {anomaly['severity']}"