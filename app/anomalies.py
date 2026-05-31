from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import EventORM
from datetime import datetime, timezone, timedelta, date

router = APIRouter()


@router.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str, db: Session = Depends(get_db)):
    now         = datetime.now(timezone.utc)
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    anomalies   = []

    # 1. Billing queue spike
    latest_queue = db.query(EventORM).filter(
        EventORM.store_id  == store_id,
        EventORM.event_type == "BILLING_QUEUE_JOIN",
        EventORM.is_staff  == False,
    ).order_by(EventORM.timestamp.desc()).first()

    if latest_queue:
        depth = (latest_queue.metadata_ or {}).get("queue_depth", 0)
        if depth and depth > 5:
            anomalies.append({
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL",
                "description": f"Queue depth is {depth} — exceeds threshold of 5",
                "suggested_action": "Open additional billing counter immediately",
                "detected_at": now.isoformat(),
            })

    # 2. Dead zone (no visits in last 30 min)
    thirty_min_ago = now - timedelta(minutes=30)
    active_zones = {
        r.zone_id for r in db.query(EventORM).filter(
            EventORM.store_id  == store_id,
            EventORM.event_type == "ZONE_ENTER",
            EventORM.timestamp  >= thirty_min_ago,
            EventORM.is_staff  == False,
        ).all() if r.zone_id
    }
    all_zones = {
        r.zone_id for r in db.query(EventORM).filter(
            EventORM.store_id  == store_id,
            EventORM.zone_id   != None,
            EventORM.timestamp >= today_start,
        ).all() if r.zone_id
    }
    for zone in all_zones - active_zones:
        anomalies.append({
            "type": "DEAD_ZONE",
            "severity": "INFO",
            "description": f"No visits in zone '{zone}' for 30+ minutes",
            "suggested_action": f"Check if zone '{zone}' display or signage needs attention",
            "detected_at": now.isoformat(),
        })

    return {"store_id": store_id, "anomalies": anomalies, "checked_at": now.isoformat()}