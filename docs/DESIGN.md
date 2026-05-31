# Store Intelligence System — Architecture Design

## Overview

This system ingests raw CCTV footage from a retail store and produces real-time business analytics via a REST API. The north star metric is **offline store conversion rate**: visitors who completed a purchase divided by total unique visitors.

The system is built in four stages: video detection, event emission, API ingest, and analytics queries.

---

## Architecture

```
CCTV Clips (.mp4)
      │
      ▼
[Detection Layer]  detect.py
  YOLOv8m + ByteTrack
  → per-frame bounding boxes + track_ids
      │
      ▼
[Tracking Layer]  tracker.py
  Direction inference (entry/exit)
  Zone mapping (floor cameras)
  Staff detection heuristic
  Re-entry detection
  → structured events (.jsonl)
      │
      ▼
[Ingest API]  POST /events/ingest
  Pydantic validation per-event
  Idempotent upsert (event_id PK)
  PostgreSQL storage
      │
      ▼
[Analytics API]  GET /stores/{id}/metrics|funnel|heatmap|anomalies
  Real-time SQL aggregations
  No caching — always queries live data
```

---

## Data Flow: From Frame to Conversion Rate

A single customer entering the store produces the following chain:

1. YOLOv8m detects a person bounding box in frame 90 of CAM 1 (entry camera)
2. ByteTrack assigns `track_id=7` and maintains it across subsequent frames
3. `tracker.py` sees the centroid move downward in the frame (top → bottom = entering store direction) and emits an `ENTRY` event with a generated `visitor_id=VIS_a0fb3d`
4. `run.sh` pipes the events into `POST /events/ingest`
5. The ingest endpoint validates, deduplicates by `event_id`, and stores in PostgreSQL
6. `GET /stores/ST1008/metrics` queries the DB: counts distinct `visitor_id` values with ENTRY events, counts distinct visitors who also had `BILLING_QUEUE_JOIN`, divides to get `conversion_rate`

---

## AI-Assisted Decisions

### 1. ByteTrack over DeepSORT for person tracking

I asked Claude to compare ByteTrack, DeepSORT, and StrongSORT for retail CCTV conditions (15fps, partial occlusion, crowded billing scenes). Claude recommended ByteTrack for three reasons: it handles occlusion by using IoU-based re-identification rather than appearance features, it has no dependency on a separate Re-ID model, and it is built into Ultralytics so no separate install is required. I agreed and implemented it. The tradeoff Claude acknowledged: ByteTrack loses track identity when two people cross paths, which can cause re-entry misdetection. I mitigated this with a 10-minute re-entry window — if the same `track_id` reappears within 10 minutes, it is treated as a REENTRY rather than a new ENTRY.

### 2. Unified event schema with nullable fields

I asked Claude to design the event schema. It initially proposed separate schemas per event type (EntryEvent, ZoneEvent, BillingEvent) for strict type safety. I overrode this decision and chose a single unified schema with nullable fields. My reasoning: a single schema means a single DB table, a single ingest endpoint, and a single deduplication key (`event_id`). The type safety concern is addressed by a Pydantic validator that enforces `zone_id` is non-null for zone events. The tradeoff is that the schema is more permissive, but the validator catches violations at ingest time, not at query time.

### 3. Per-event validation in ingest endpoint

The ingest endpoint originally used Pydantic's `EventBatch` model to validate all events at once. Claude's initial suggestion used batch-level validation, which meant one malformed event in a batch of 500 would reject all 500. I discovered this when writing tests and changed the design: the endpoint now accepts a raw `dict`, iterates over events, and validates each independently with a try/except around `Event(**raw)`. Valid events are stored, invalid ones are collected into an `errors` list. This is the correct behaviour for a production ingest pipeline where partial success is preferable to total failure.

---

## Edge Case Handling

**Group entry**: YOLOv8 detects individual bounding boxes, not groups. Three people entering simultaneously produce three separate track_ids and three ENTRY events. This is correct behaviour.

**Staff exclusion**: `tracker.py` uses a heuristic — if a track spends more than 60% of its lifetime in the top or bottom 15% of the frame (near walls/edges where staff typically move), it is flagged `is_staff=True`. All API endpoints filter `is_staff=False` before computing customer metrics.

**Re-entry**: When a `visitor_id` has a prior EXIT event and reappears within 10 minutes, the pipeline emits `REENTRY` instead of `ENTRY`. The funnel endpoint counts ENTRY and REENTRY as the same visitor (deduplicates by `visitor_id`).

**Camera overlap**: CAM 1 (entry) and CAM 2 (floor) may detect the same person. Because each camera assigns independent `visitor_id` values (there is no cross-camera Re-ID model), the metrics endpoint uses a heuristic: `unique_visitors = max(entry_count, total_unique // 3)`. This is documented in CHOICES.md as a known limitation.

**Empty store**: All API endpoints return zero values (not null, not 500) when no events exist for a store. Tested explicitly in `test_metrics_empty_store`.

**Zero-traffic periods**: The detection pipeline skips frames with no detections and continues normally. No crash, no null output.

---

## Technology Choices

| Component | Technology | Reason |
|-----------|------------|--------|
| Detection | YOLOv8m | Balanced accuracy/speed for CPU inference |
| Tracking | ByteTrack (via Ultralytics) | No separate Re-ID model required |
| API framework | FastAPI | Automatic OpenAPI docs, Pydantic integration |
| Database | PostgreSQL 15 | JSONB for metadata, reliable ACID transactions |
| ORM | SQLAlchemy 2.0 | Works with both PostgreSQL (production) and SQLite (tests) |
| Containerisation | Docker Compose | Single-command startup, isolated DB |

---

## Known Limitations

**Cross-camera visitor identity**: The system cannot reliably link the same physical person across cameras. CAM 1 assigns `VIS_abc`, CAM 2 assigns `VIS_xyz` for the same person. The unique visitor count is therefore a heuristic approximation, not an exact count. A production deployment would require an OSNet Re-ID model or embedding-based matching.

**Staff detection accuracy**: The edge-proximity heuristic works well for staff who stay near walls, but may misclassify customers who browse perimeter shelving as staff.

**Conversion rate precision**: The system correlates billing zone presence with POS transactions using a 5-minute time window. This is an approximation — a customer who pays and leaves quickly may be missed if the billing camera did not capture them.