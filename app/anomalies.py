from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import distinct
from app.database import get_db
from app.models import EventORM
from datetime import datetime, timezone, timedelta, date

router = APIRouter()


@router.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str, db: Session = Depends(get_db)):
    now         = datetime.now(timezone.utc)
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    anomalies   = []

    # ── 1. Billing queue spike ────────────────────────────────────────────────
    latest_queue = db.query(EventORM).filter(
        EventORM.store_id   == store_id,
        EventORM.event_type == "BILLING_QUEUE_JOIN",
        EventORM.is_staff   == False,
    ).order_by(EventORM.timestamp.desc()).first()

    if latest_queue:
        depth = (latest_queue.metadata_ or {}).get("queue_depth", 0)
        if depth and depth > 5:
            anomalies.append({
                "type":             "BILLING_QUEUE_SPIKE",
                "severity":         "CRITICAL",
                "description":      f"Queue depth is {depth} — exceeds threshold of 5",
                "suggested_action": "Open additional billing counter immediately",
                "detected_at":      now.isoformat(),
            })

    # ── 2. Dead zone (no visits in last 30 min) ───────────────────────────────
    thirty_min_ago = now - timedelta(minutes=30)

    active_zones = {
        r.zone_id for r in db.query(EventORM).filter(
            EventORM.store_id   == store_id,
            EventORM.event_type == "ZONE_ENTER",
            EventORM.timestamp  >= thirty_min_ago,
            EventORM.is_staff   == False,
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
            "type":             "DEAD_ZONE",
            "severity":         "INFO",
            "description":      f"No visits in zone '{zone}' for 30+ minutes",
            "suggested_action": f"Check if zone '{zone}' display or signage needs attention",
            "detected_at":      now.isoformat(),
        })

    # ── 3. Conversion drop vs 7-day average ──────────────────────────────────
    # Today's conversion rate
    today_end = datetime.combine(date.today(), datetime.max.time()).replace(tzinfo=timezone.utc)

    def conversion_for_window(start, end):
        """Return conversion rate for a given time window. Returns None if no data."""
        def q(event_types=None):
            base = db.query(EventORM).filter(
                EventORM.store_id  == store_id,
                EventORM.is_staff  == False,
                EventORM.timestamp >= start,
                EventORM.timestamp <= end,
            )
            if event_types:
                base = base.filter(EventORM.event_type.in_(event_types))
            return base

        unique_visitors = q(["ENTRY", "REENTRY"]).with_entities(
            distinct(EventORM.visitor_id)
        ).count()

        if unique_visitors == 0:
            return None

        converted = q(["BILLING_QUEUE_JOIN"]).with_entities(
            distinct(EventORM.visitor_id)
        ).count()
        abandoned = q(["BILLING_QUEUE_ABANDON"]).with_entities(
            distinct(EventORM.visitor_id)
        ).count()
        purchased = max(0, converted - abandoned)
        return purchased / unique_visitors

    today_rate = conversion_for_window(today_start, today_end)

    if today_rate is not None:
        # Collect daily rates for the previous 7 days
        historical_rates = []
        for days_back in range(1, 8):
            day = date.today() - timedelta(days=days_back)
            w_start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
            w_end   = datetime.combine(day, datetime.max.time()).replace(tzinfo=timezone.utc)
            rate = conversion_for_window(w_start, w_end)
            if rate is not None:
                historical_rates.append(rate)

        if historical_rates:
            avg_7d = sum(historical_rates) / len(historical_rates)
            # Flag if today's rate is more than 20% below the 7-day average
            if avg_7d > 0 and today_rate < avg_7d * 0.8:
                drop_pct = round((avg_7d - today_rate) / avg_7d * 100, 1)
                anomalies.append({
                    "type":             "CONVERSION_DROP",
                    "severity":         "WARN",
                    "description":      (
                        f"Conversion rate today ({today_rate:.1%}) is {drop_pct}% below "
                        f"7-day average ({avg_7d:.1%})"
                    ),
                    "suggested_action": (
                        "Review funnel drop-off — check billing zone staffing "
                        "and queue wait times"
                    ),
                    "detected_at":      now.isoformat(),
                })

    return {
        "store_id":   store_id,
        "anomalies":  anomalies,
        "checked_at": now.isoformat(),
    }