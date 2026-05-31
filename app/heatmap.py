from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import EventORM
from datetime import datetime, timezone

router = APIRouter()


@router.get("/stores/{store_id}/heatmap")
def get_heatmap(
    store_id: str,
    date: str = Query(default=None),
    db: Session = Depends(get_db)
):
    if date:
        try:
            query_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            query_date = datetime.now(timezone.utc).date()
    else:
        query_date = datetime.now(timezone.utc).date()

    day_start = datetime(query_date.year, query_date.month, query_date.day, 0, 0, 0, tzinfo=timezone.utc)
    day_end   = datetime(query_date.year, query_date.month, query_date.day, 23, 59, 59, tzinfo=timezone.utc)

    # Get visit counts and avg dwell per zone
    zone_enters = db.query(EventORM).filter(
        EventORM.store_id  == store_id,
        EventORM.event_type == "ZONE_ENTER",
        EventORM.is_staff  == False,
        EventORM.timestamp >= day_start,
        EventORM.timestamp <= day_end,
    ).all()

    zone_dwells = db.query(EventORM).filter(
        EventORM.store_id  == store_id,
        EventORM.event_type == "ZONE_DWELL",
        EventORM.is_staff  == False,
        EventORM.timestamp >= day_start,
        EventORM.timestamp <= day_end,
    ).all()

    # Aggregate by zone
    visit_counts: dict = {}
    dwell_totals: dict = {}
    dwell_counts: dict = {}

    for e in zone_enters:
        if e.zone_id:
            visit_counts[e.zone_id] = visit_counts.get(e.zone_id, 0) + 1

    for e in zone_dwells:
        if e.zone_id:
            dwell_totals[e.zone_id] = dwell_totals.get(e.zone_id, 0) + (e.dwell_ms or 0)
            dwell_counts[e.zone_id] = dwell_counts.get(e.zone_id, 0) + 1

    if not visit_counts:
        return {"store_id": store_id, "date": str(query_date), "zones": []}

    max_visits = max(visit_counts.values()) or 1
    total_sessions = len(set(e.visitor_id for e in zone_enters))

    zones = []
    for zone_id, count in sorted(visit_counts.items(), key=lambda x: -x[1]):
        avg_dwell = (dwell_totals.get(zone_id, 0) / dwell_counts[zone_id]) if dwell_counts.get(zone_id) else 0
        score = round((count / max_visits) * 100)
        confidence = "LOW" if total_sessions < 20 else "HIGH"
        zones.append({
            "zone_id":         zone_id,
            "visit_count":     count,
            "avg_dwell_ms":    int(avg_dwell),
            "score":           score,
            "data_confidence": confidence,
        })

    return {
        "store_id": store_id,
        "date":     str(query_date),
        "zones":    zones,
    }