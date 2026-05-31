from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.models import EventORM
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import csv
import os

router = APIRouter()

POS_CSV_PATH = os.environ.get("POS_CSV_PATH", "/app/data/pos_transactions.csv")

# ---------------------------------------------------------------------------
# POS correlation helper
# ---------------------------------------------------------------------------

def load_pos_transactions(store_id: str, day_start: datetime, day_end: datetime) -> list:
    """
    Load POS transactions for a given store and date window from the CSV.
    Returns a list of transaction timestamps (datetime, UTC-aware).
    Falls back gracefully to [] if the file is missing or unreadable.
    """
    transactions = []
    if not os.path.exists(POS_CSV_PATH):
        return transactions
    try:
        with open(POS_CSV_PATH, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("store_id", "").strip() != store_id:
                    continue
                raw_ts = row.get("timestamp", "").strip()
                if not raw_ts:
                    continue
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    if day_start <= ts <= day_end:
                        transactions.append(ts)
                except ValueError:
                    continue
    except Exception:
        pass
    return transactions


def correlate_purchases_with_pos(
    billing_visitor_timestamps: dict,   # visitor_id -> earliest BILLING_QUEUE_JOIN timestamp
    pos_transactions: list,             # list of datetime
    window_seconds: int = 300,          # 5-minute window per problem spec
) -> set:
    """
    A visitor counts as a converted (purchased) visitor if they had a
    BILLING_QUEUE_JOIN event within `window_seconds` before any POS transaction.

    Problem spec: "A visitor who was in the billing zone in the 5-minute window
    before a transaction timestamp counts as a converted visitor for that session."

    Returns the set of visitor_ids who are correlated with a purchase.
    """
    if not pos_transactions or not billing_visitor_timestamps:
        return set()

    purchased = set()
    for vid, billing_ts in billing_visitor_timestamps.items():
        for txn_ts in pos_transactions:
            # Visitor must have been in billing zone in the 5 min before the transaction
            diff = (txn_ts - billing_ts).total_seconds()
            if 0 <= diff <= window_seconds:
                purchased.add(vid)
                break  # one matching transaction is enough

    return purchased


# ---------------------------------------------------------------------------
# Funnel endpoint
# ---------------------------------------------------------------------------

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

    day_start = datetime(query_date.year, query_date.month, query_date.day,
                         0, 0, 0, tzinfo=timezone.utc)
    day_end   = datetime(query_date.year, query_date.month, query_date.day,
                         23, 59, 59, tzinfo=timezone.utc)

    events = db.query(EventORM).filter(
        EventORM.store_id  == store_id,
        EventORM.is_staff  == False,
        EventORM.timestamp >= day_start,
        EventORM.timestamp <= day_end,
    ).order_by(EventORM.timestamp).all()

    # Build per-visitor event type sets and earliest billing timestamp
    visitor_events    = defaultdict(list)
    billing_timestamps = {}   # visitor_id -> earliest BILLING_QUEUE_JOIN datetime

    for e in events:
        visitor_events[e.visitor_id].append(e.event_type)
        if e.event_type == "BILLING_QUEUE_JOIN":
            ts = e.timestamp
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if not ts.tzinfo:
                ts = ts.replace(tzinfo=timezone.utc)
            if e.visitor_id not in billing_timestamps:
                billing_timestamps[e.visitor_id] = ts

    # Stage sets
    unique_entrants  = set()
    zone_visitors    = set()
    billing_visitors = set()

    for vid, etypes in visitor_events.items():
        if "ENTRY" in etypes or "REENTRY" in etypes:
            unique_entrants.add(vid)
        if any(t in etypes for t in ["ZONE_ENTER", "ZONE_DWELL"]):
            zone_visitors.add(vid)
        if "BILLING_QUEUE_JOIN" in etypes:
            billing_visitors.add(vid)

    # POS-correlated purchase visitors
    pos_transactions  = load_pos_transactions(store_id, day_start, day_end)
    pos_available     = len(pos_transactions) > 0

    if pos_available:
        # Use POS correlation per problem spec
        billing_in_funnel = {
            vid: ts for vid, ts in billing_timestamps.items()
            if vid in unique_entrants
        }
        purchase_visitors = correlate_purchases_with_pos(billing_in_funnel, pos_transactions)
    else:
        # Fallback: BILLING_QUEUE_JOIN without ABANDON = proxy purchase
        # Used when POS CSV is absent (e.g. test environments)
        purchase_visitors = set()
        for vid, etypes in visitor_events.items():
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
        "pos_correlated": pos_available,
        "funnel": [
            {"stage": "Entry",         "visitors": n_entry,    "dropoff_pct": 0.0},
            {"stage": "Zone Visit",    "visitors": n_zone,     "dropoff_pct": dropoff(n_zone, n_entry)},
            {"stage": "Billing Queue", "visitors": n_billing,  "dropoff_pct": dropoff(n_billing, n_zone)},
            {"stage": "Purchase",      "visitors": n_purchase, "dropoff_pct": dropoff(n_purchase, n_billing)},
        ]
    }