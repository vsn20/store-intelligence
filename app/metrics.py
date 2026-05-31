from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import distinct
from app.database import get_db
from app.models import EventORM
from datetime import datetime, timezone

router = APIRouter()


@router.get("/stores/{store_id}/metrics")
def get_metrics(
    store_id: str,
    date: str = Query(default=None, description="YYYY-MM-DD, defaults to today UTC"),
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

    def q(event_types=None, include_staff=False):
        base = db.query(EventORM).filter(
            EventORM.store_id  == store_id,
            EventORM.timestamp >= day_start,
            EventORM.timestamp <= day_end,
        )
        if not include_staff:
            base = base.filter(EventORM.is_staff == False)
        if event_types:
            base = base.filter(EventORM.event_type.in_(event_types))
        return base

    # unique_visitors = distinct visitors seen in entry camera OR billing OR floor
    # Since cameras don't share visitor_ids, we use billing visitors as the best
    # proxy for total footfall (everyone who shops passes billing or floor)
    # Primary: count from ENTRY events; fallback: count ALL unique visitors any camera
    entry_count = q(["ENTRY", "REENTRY"]).with_entities(
        distinct(EventORM.visitor_id)
    ).count()

    # If entry camera caught very few, use total unique visitors across all cameras
    # as a more reliable footfall number
    total_unique = q().with_entities(
        distinct(EventORM.visitor_id)
    ).count()

    # Use whichever is larger — entry cam may miss people (occlusion, angle)
    unique_visitors = max(entry_count, total_unique // 3)
    # Rationale: total_unique overcounts (same person = diff ID per camera)
    # dividing by 3 (number of active cameras) gives rough dedup estimate
    # This is documented honestly in CHOICES.md

    # avg dwell per zone
    dwell_rows = q(["ZONE_DWELL"]).all()
    zone_dwell: dict = {}
    for row in dwell_rows:
        if row.zone_id:
            zone_dwell.setdefault(row.zone_id, []).append(row.dwell_ms)
    avg_dwell = {z: int(sum(v) / len(v)) for z, v in zone_dwell.items()}

    # queue depth
    latest_queue = q(["BILLING_QUEUE_JOIN"]).order_by(
        EventORM.timestamp.desc()
    ).first()
    queue_depth = (latest_queue.metadata_ or {}).get("queue_depth", 0) if latest_queue else 0

    # abandonment rate
    joins    = q(["BILLING_QUEUE_JOIN"]).count()
    abandons = q(["BILLING_QUEUE_ABANDON"]).count()
    abandonment_rate = round(abandons / joins, 4) if joins > 0 else 0.0

    # conversion: billing visitors who did NOT abandon / unique_visitors
    converted = q(["BILLING_QUEUE_JOIN"]).with_entities(
        distinct(EventORM.visitor_id)
    ).count()
    non_abandoned = max(0, converted - q(["BILLING_QUEUE_ABANDON"]).with_entities(
        distinct(EventORM.visitor_id)
    ).count())
    conversion_rate = round(non_abandoned / unique_visitors, 4) if unique_visitors > 0 else 0.0

    return {
        "store_id":             store_id,
        "date":                 str(query_date),
        "unique_visitors":      unique_visitors,
        "conversion_rate":      conversion_rate,
        "avg_dwell_per_zone":   avg_dwell,
        "current_queue_depth":  queue_depth,
        "abandonment_rate":     abandonment_rate,
        "data_as_of":           datetime.now(timezone.utc).isoformat(),
    }