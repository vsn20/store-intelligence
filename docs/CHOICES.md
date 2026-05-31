# Engineering Choices

Three decisions I made building this system, with full reasoning.

---

## Decision 1: Detection Model — YOLOv8m

### Options Considered

| Model | Speed (CPU) | Accuracy | Notes |
|-------|-------------|----------|-------|
| YOLOv8n | ~15ms/frame | mAP 37.3 | Fast but misses partial occlusions |
| YOLOv8m | ~50ms/frame | mAP 50.2 | Balanced — my choice |
| YOLOv8x | ~200ms/frame | mAP 53.9 | Too slow for near-real-time |
| RT-DETR | ~80ms/frame | mAP 53.0 | Better for crowded scenes, complex setup |

### What AI Suggested

I asked Claude: *"Which YOLO variant should I use for retail CCTV at 15fps with partial
occlusion?"* Claude suggested YOLOv8m as the starting point and noted that RT-DETR would
perform better in crowded billing scenes. It suggested I benchmark both on a sample clip
before deciding.

### What I Chose and Why

I chose YOLOv8m. The clips are 15fps and I process every 3rd frame (effectively 5fps),
which gives me 200ms per frame budget on CPU — YOLOv8m fits comfortably. RT-DETR's higher
accuracy in crowds is appealing for the billing camera, but it requires a separate setup
(no built-in ByteTrack integration) and adds installation complexity. YOLOv8m with
ByteTrack (both in the Ultralytics package) is a single `pip install ultralytics`.

I agreed with the AI's reasoning on model accuracy but overrode the RT-DETR suggestion
because the integration cost was not worth a ~3 mAP point improvement for this challenge's
time constraints.

**Trade-off acknowledged**: YOLOv8m struggles with heavy occlusion in the billing queue.
I mitigated this by lowering the confidence threshold from the default 0.5 to 0.35, which
increases recall at the cost of some false positives.

---

## Decision 2: Event Schema Design

### Options Considered

**Option A — Separate schemas per event type**: `EntryEvent`, `ZoneEvent`, `BillingEvent`
each with strict required fields. Maximum type safety.

**Option B — Unified schema with nullable fields**: Single `Event` model with nullable
`zone_id`, `dwell_ms`, `queue_depth`. One DB table, one ingest endpoint.

**Option C — EAV (entity-attribute-value)**: Generic key-value store. Maximum flexibility,
terrible for querying.

### What AI Suggested

Claude initially recommended Option A (separate schemas), arguing that it prevents invalid
combinations like a ZONE_DWELL event with a null `zone_id`. It generated three separate
Pydantic models with strict field requirements.

### What I Chose and Why

I chose Option B (unified schema), overriding Claude's recommendation. My reasoning:

1. **Single ingest endpoint**: One `POST /events/ingest` handles all event types. The
   alternative would be multiple endpoints or a discriminated union, both harder to test.
2. **Single DB table**: All events in one table means all analytics queries are simple
   `WHERE event_type = 'X'` filters. No JOINs needed.
3. **Single deduplication key**: `event_id` as primary key handles idempotency for all
   event types uniformly.
4. **Validation at boundary**: A Pydantic `field_validator` on `zone_id` enforces that
   zone events have a non-null zone_id. The constraint is enforced at ingest, not at
   schema definition.

The schema includes a `metadata` JSONB field for event-type-specific data (`queue_depth`
for billing events, `sku_zone` for zone events). This keeps the core schema fixed while
allowing extensibility.

**Trade-off**: The schema is slightly more permissive than Option A — a developer could
send a ZONE_DWELL with `dwell_ms=0` and it would pass validation. I accepted this
trade-off because the detection pipeline always sets correct values, and the validator
catches the critical constraint (zone_id).

---

## Decision 3: Storage Engine — PostgreSQL without TimescaleDB

### Options Considered

**PostgreSQL (standard)**: Reliable, ACID, JSONB support, familiar. No time-series
optimisation.

**PostgreSQL + TimescaleDB**: Automatic partitioning by time, faster time-range queries.
Adds installation complexity.

**SQLite**: Simpler, no separate container. No JSONB, no concurrent writes, not suitable
for production.

**Redis Streams**: Real-time event streaming, sub-millisecond reads. No persistent
analytics, complex operational model.

### What AI Suggested

Claude suggested PostgreSQL with TimescaleDB, citing that retail event data is inherently
time-series (all queries filter by timestamp) and TimescaleDB's automatic hypertable
partitioning would keep queries fast as data grows. It provided the Docker Compose snippet
to add TimescaleDB.

### What I Chose and Why

I chose standard PostgreSQL, overriding the TimescaleDB suggestion.

My reasoning: this challenge involves 5 store cameras with 20-minute clips — roughly
20,000 events total. TimescaleDB's benefits materialise at millions of rows per day. For
this scale, a standard B-tree index on `(store_id, timestamp)` is sufficient. Adding
TimescaleDB introduces:

1. A different Docker image (`timescale/timescaledb` instead of `postgres`)
2. A `CREATE EXTENSION timescaledb` migration step
3. A `SELECT create_hypertable('events', 'timestamp')` DDL step
4. A potential failure point during the acceptance gate review

None of these are complex, but they add friction to `docker compose up` with no
measurable benefit at this data scale. I documented this explicitly: if this system were
to handle 40 live stores in production (as described in the problem statement),
TimescaleDB would be the correct next step.

**What would make me change this decision**: If the API's anomaly detection queries (which
scan the last 30 minutes of data) showed latency above 100ms under load testing, I would
add TimescaleDB. At the current scale, these queries complete in under 5ms.

---

## Decision 4: Re-entry Test Architecture

### The Problem

The challenge rubric requires testing REENTRY event detection. The tracker's re-entry
logic works as follows: when a `visitor_id` has a prior EXIT recorded in `visitor_exited`,
and the same visitor reappears within 10 minutes, the next event is emitted as `REENTRY`
instead of `ENTRY`.

In production, this works via Re-ID — an OSNet model matches the appearance of a new
`track_id` to a previous `visitor_id`. In the current pipeline (ByteTrack only, no Re-ID
model), re-entry across different track IDs is not supported.

### Options Considered

**Option A — End-to-end subprocess test**: Run the tracker CLI with three sequential
detection batches (enter → exit → re-enter) using the same `track_id`. Problem: the
tracker's `emitted_entries` set guards against firing twice on the same `track_id`, so
the re-entry pass is silently skipped. Zero events emitted on the third pass.

**Option B — Mock the Re-ID layer**: Patch `track_to_visitor` to force two different
`track_id` values to share the same `visitor_id`, simulating what Re-ID would do. Problem:
`track_to_visitor` and `visitor_exited` are local variables inside `main()` — not
patchable without refactoring the tracker into a class.

**Option C — Test the decision logic directly**: Extract the REENTRY decision into a pure
function and test it with pre-seeded state. This tests exactly what matters — the 10-minute
window boundary — without requiring a Re-ID model or subprocess invocation.

### What AI Suggested

Claude initially wrote an end-to-end test using the same `track_id` across three passes
(Option A). When this failed (0 events emitted on the re-entry pass), Claude diagnosed
the `emitted_entries` guard and suggested Option B. I evaluated Option B but rejected it
because patching local variables in a function requires `unittest.mock.patch` at the
call site, which is fragile and tightly coupled to the implementation.

### What I Chose and Why

I chose Option C: `test_reentry_logic_via_state_injection` replicates the exact
`if visitor_id in visitor_exited` branch from `tracker.py` and tests it with controlled
state. The test covers:

- Re-entry within 10 minutes → `REENTRY`
- Re-entry after 10 minutes → new `ENTRY`
- First-time visitor (not in `visitor_exited`) → `ENTRY`

A companion test (`test_exit_populates_visitor_exited_state`) verifies that EXIT events
are emitted correctly — which is the prerequisite for re-entry detection to ever fire.

**Trade-off**: Option C does not test the full end-to-end re-entry path. I accepted this
because the full path requires a Re-ID model that is explicitly out of scope for the
current implementation. The test documents this constraint clearly and is honest about
what is and is not tested.

**What would make me change this**: If I added OSNet Re-ID to the pipeline (the correct
production approach), I would replace Option C with a full end-to-end test that injects
appearance embeddings and verifies the `visitor_id` mapping across two track IDs.
---

## Decision 5: POS Transaction Correlation for Conversion Rate

### The Problem

The problem spec defines conversion as: *"A visitor who was in the billing zone
in the 5-minute window before a transaction timestamp counts as a converted
visitor for that session."* The original implementation used
`BILLING_QUEUE_JOIN minus BILLING_QUEUE_ABANDON` as a proxy for purchase,
which does not use the actual POS data at all.

### Options Considered

**Option A — No-abandon proxy**: `BILLING_QUEUE_JOIN` without a subsequent
`BILLING_QUEUE_ABANDON` = purchased. Simple, no file I/O, works in tests.
Problem: over-counts — a visitor who joined the queue and left without
explicitly abandoning (camera lost them) is incorrectly counted as converted.

**Option B — POS correlation per spec**: Load `pos_transactions.csv`, for each
billing visitor check if their `BILLING_QUEUE_JOIN` timestamp falls within 300
seconds before any POS transaction in the same store. This is exactly what the
spec requires.

**Option C — Hybrid with fallback**: Use POS correlation when the CSV exists,
fall back to Option A when it doesn't (e.g. test environments without the CSV).

### What AI Suggested

Claude did not flag this gap in the original implementation — it accepted the
no-abandon proxy without comparing it to the spec's explicit definition. I
identified the mismatch by re-reading the spec's conversion definition and
comparing it to the code's purchase proxy logic.

### What I Chose and Why

Option C: the hybrid approach. `load_pos_transactions()` and
`correlate_purchases_with_pos()` are implemented in `funnel.py` and reused
in `anomalies.py`. When the CSV is present, POS correlation is used. When
absent, the no-abandon proxy is the fallback so tests continue to work without
needing a CSV fixture.

The funnel response includes `"pos_correlated": true/false` so callers know
which method produced the conversion count. This is important for operational
transparency — an on-call engineer seeing a conversion rate anomaly should know
whether it is based on actual POS data or an approximation.

**Trade-off**: POS correlation requires the CSV to be mounted at a known path
(`POS_CSV_PATH` env var, default `/app/data/pos_transactions.csv`). If the CSV
is not mounted, the API silently falls back to the proxy. This is documented in
the README. A production deployment would use a database-backed POS feed instead
of a CSV file.