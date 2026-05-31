import time
import uuid
import os
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.models import EventORM, IngestError, IngestResponse
import logging

logger = logging.getLogger("purplle.ingest")
router = APIRouter()

_is_postgres = "postgresql" in os.getenv("DATABASE_URL", "sqlite")


@router.post("/events/ingest", response_model=IngestResponse)
def ingest_events(payload: dict, db: Session = Depends(get_db)):
    from app.models import Event
    from pydantic import ValidationError

    trace_id   = str(uuid.uuid4())
    start_time = time.time()
    accepted, rejected, errors = 0, 0, []

    raw_events = payload.get("events", [])
    if not isinstance(raw_events, list):
        return IngestResponse(accepted=0, rejected=0, errors=[])

    for raw in raw_events:
        event_id = str(raw.get("event_id", uuid.uuid4()))
        try:
            event = Event(**raw)
        except (ValidationError, Exception) as e:
            rejected += 1
            errors.append(IngestError(event_id=event_id, reason=str(e)))
            continue

        try:
            # Check if already exists (idempotency) — works on both DBs
            existing = db.query(EventORM).filter(
                EventORM.event_id == str(event.event_id)
            ).first()
            if existing:
                accepted += 1  # already stored, count as accepted (idempotent)
                continue

            row = EventORM(
                event_id   = str(event.event_id),
                store_id   = event.store_id,
                camera_id  = event.camera_id,
                visitor_id = event.visitor_id,
                event_type = event.event_type.value,
                timestamp  = event.timestamp,
                zone_id    = event.zone_id,
                dwell_ms   = event.dwell_ms,
                is_staff   = event.is_staff,
                confidence = event.confidence,
                metadata_  = event.metadata.model_dump() if event.metadata else None,
            )
            db.add(row)
            db.flush()  # write to DB within transaction, catch constraint errors
            accepted += 1

        except Exception as e:
            db.rollback()
            rejected += 1
            errors.append(IngestError(event_id=event_id, reason=str(e)))
            continue

    try:
        db.commit()
    except Exception as e:
        db.rollback()

    latency_ms = int((time.time() - start_time) * 1000)
    store_id   = raw_events[0].get("store_id", "unknown") if raw_events else "unknown"
    logger.info(
        "ingest",
        extra=dict(trace_id=trace_id, store_id=store_id, event_count=len(raw_events),
                   accepted=accepted, rejected=rejected, latency_ms=latency_ms, status_code=200)
    )
    return IngestResponse(accepted=accepted, rejected=rejected, errors=errors)