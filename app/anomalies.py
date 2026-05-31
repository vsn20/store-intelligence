from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import distinct
from app.database import get_db
from app.models import EventORM
from app.funnel import load_pos_transactions, correlate_purchases_with_pos
from datetime import datetime, timezone, timedelta, date
from collections import defaultdict

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
    today_end = datetime.combine(date.today(), datetime.max.time()).replace(tzinfo=timezone.utc)

    def conversion_for_window(start: datetime, end: datetime):
        """
        Compute conversion rate for a time window.
        Uses POS transaction correlation when pos_transactions.csv is available,
        falls back to BILLING_QUEUE_JOIN minus ABANDON proxy otherwise.
        Returns None if no visitor data exists for the window.
        """
        base_events = db.query(EventORM).filter(
            EventORM.store_id  == store_id,
            EventORM.is_staff  == False,
            EventORM.timestamp >= start,
            EventORM.timestamp <= end,
        ).all()

        if not base_events:
            return None

        # Build visitor sets
        visitor_events     = defaultdict(list)
        billing_timestamps = {}

        for e in base_events:
            visitor_events[e.visitor_id].append(e.event_type)
            if e.event_type == "BILLING_QUEUE_JOIN":
                ts = e.timestamp
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if not ts.tzinfo:
                    ts = ts.replace(tzinfo=timezone.utc)
                if e.visitor_id not in billing_timestamps:
                    billing_timestamps[e.visitor_id] = ts

        unique_visitors = {
            vid for vid, etypes in visitor_events.items()
            if "ENTRY" in etypes or "REENTRY" in etypes
        }

        if not unique_visitors:
            return None

        # POS-correlated purchase count
        pos_transactions = load_pos_transactions(store_id, start, end)

        if pos_transactions:
            billing_in_window = {
                vid: ts for vid, ts in billing_timestamps.items()
                if vid in unique_visitors
            }
            purchased = correlate_purchases_with_pos(billing_in_window, pos_transactions)
        else:
            # Fallback: no-abandon proxy
            purchased = {
                vid for vid, etypes in visitor_events.items()
                if vid in unique_visitors
                and "BILLING_QUEUE_JOIN" in etypes
                and "BILLING_QUEUE_ABANDON" not in etypes
            }

        return len(purchased) / len(unique_visitors)

    today_rate = conversion_for_window(today_start, today_end)

    if today_rate is not None:
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