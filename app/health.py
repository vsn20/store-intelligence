from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import EventORM
from datetime import datetime, timezone, timedelta

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(minutes=10)

    try:
        rows = (
            db.query(EventORM.store_id, func.max(EventORM.timestamp).label("last_event"))
            .group_by(EventORM.store_id)
            .all()
        )
    except Exception as e:
        # Return proper 503 — FastAPI does NOT support Flask-style tuple returns.
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "error": "database_unavailable",
                "detail": str(e),
            },
        )

    last_event_per_store = {}
    stale_feeds          = []

    for store_id, last_ts in rows:
        last_event_per_store[store_id] = last_ts.isoformat() if last_ts else None
        # Ensure tz-aware comparison
        if last_ts is None:
            stale_feeds.append(store_id)
        else:
            ts_aware = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc)
            if ts_aware < stale_threshold:
                stale_feeds.append(store_id)

    return {
        "status":               "ok",
        "last_event_per_store": last_event_per_store,
        "stale_feeds":          stale_feeds,
        "checked_at":           now.isoformat(),
    }