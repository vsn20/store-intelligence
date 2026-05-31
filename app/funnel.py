from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import EventORM
from datetime import datetime, timezone, date as date_type
from collections import defaultdict

router = APIRouter()


@router.get("/stores/{store_id}/funnel")
def get_funnel(
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

    events = db.query(EventORM).filter(
        EventORM.store_id  == store_id,
        EventORM.is_staff  == False,
        EventORM.timestamp >= day_start,
        EventORM.timestamp <= day_end,
    ).order_by(EventORM.timestamp).all()

    visitor_events = defaultdict(list)
    for e in events:
        visitor_events[e.visitor_id].append(e.event_type)

    unique_entrants  = set()
    zone_visitors    = set()
    billing_visitors = set()
    purchase_visitors= set()

    for vid, etypes in visitor_events.items():
        if "ENTRY" in etypes or "REENTRY" in etypes:
            unique_entrants.add(vid)
        if any(t in etypes for t in ["ZONE_ENTER", "ZONE_DWELL"]):
            zone_visitors.add(vid)
        if "BILLING_QUEUE_JOIN" in etypes:
            billing_visitors.add(vid)
        if "BILLING_QUEUE_JOIN" in etypes and "BILLING_QUEUE_ABANDON" not in etypes:
            purchase_visitors.add(vid)

    def dropoff(current, previous):
        if previous == 0:
            return 0.0
        return round((previous - current) / previous * 100, 1)

    n_entry    = len(unique_entrants)
    n_zone     = len(zone_visitors & unique_entrants)
    n_billing  = len(billing_visitors & unique_entrants)
    n_purchase = len(purchase_visitors & unique_entrants)

    return {
        "store_id": store_id,
        "date": str(query_date),
        "funnel": [
            {"stage": "Entry",         "visitors": n_entry,    "dropoff_pct": 0.0},
            {"stage": "Zone Visit",    "visitors": n_zone,     "dropoff_pct": dropoff(n_zone, n_entry)},
            {"stage": "Billing Queue", "visitors": n_billing,  "dropoff_pct": dropoff(n_billing, n_zone)},
            {"stage": "Purchase",      "visitors": n_purchase, "dropoff_pct": dropoff(n_purchase, n_billing)},
        ]
    }