#!/usr/bin/env python3
# pipeline/replay.py
# Real-time event replay script — proves the pipeline feeds the dashboard live.
#
# Usage:
#   python replay.py                          # replay to localhost
#   python replay.py --api https://vsn20-store-intelligence.hf.space
#   python replay.py --speed 2.0             # 2x speed
#   python replay.py --events ../output/events.jsonl
#
# What it does:
#   1. Reads events.jsonl (output from tracker.py)
#   2. Sorts by timestamp
#   3. Replays them in order with real-time gaps between events
#      (a 5-second gap in the original footage = 5-second wait)
#   4. POSTs each event individually to /events/ingest
#   5. Prints a live log so you can see events flowing in real time
#
# This gives proof that the pipeline is genuinely connected to the API,
# not just batch-processed. Open the dashboard while this runs to see
# visitor counts update live.

import argparse
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_EVENTS = "../output/events.jsonl"
DEFAULT_API    = "http://localhost:8000"
DEFAULT_SPEED  = 1.0   # 1.0 = real time, 2.0 = 2x faster, 0 = no delay


def parse_args():
    p = argparse.ArgumentParser(description="Real-time event replay for Store Intelligence")
    p.add_argument("--events", default=DEFAULT_EVENTS,
                   help=f"Path to events.jsonl (default: {DEFAULT_EVENTS})")
    p.add_argument("--api",    default=DEFAULT_API,
                   help=f"API base URL (default: {DEFAULT_API})")
    p.add_argument("--speed",  type=float, default=DEFAULT_SPEED,
                   help="Playback speed multiplier (default: 1.0 = real time)")
    p.add_argument("--max-gap", type=float, default=5.0,
                   help="Max seconds to wait between events (default: 5.0)")
    return p.parse_args()


def load_and_sort_events(path: str) -> list:
    events = []
    p = Path(path)
    if not p.exists():
        print(f"[replay] ERROR: events file not found: {path}")
        print(f"[replay] Run detect.py + tracker.py first to generate events.")
        return []

    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Sort by timestamp
    events.sort(key=lambda e: e.get("timestamp", ""))
    print(f"[replay] Loaded {len(events)} events from {path}")
    return events


def send_event(api_url: str, event: dict) -> bool:
    try:
        resp = requests.post(
            f"{api_url}/events/ingest",
            json={"events": [event]},
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            return result.get("accepted", 0) > 0
        return False
    except Exception as e:
        print(f"[replay] Send error: {e}")
        return False


def format_event_line(event: dict, elapsed: float) -> str:
    etype   = event.get("event_type", "?").ljust(22)
    visitor = event.get("visitor_id", "?")[:12]
    zone    = event.get("zone_id") or "-"
    staff   = " [STAFF]" if event.get("is_staff") else ""
    conf    = event.get("confidence", 0)
    ts      = event.get("timestamp", "")[:19]
    return (
        f"  [{elapsed:7.1f}s] {etype} | {visitor} | zone={zone:<12} "
        f"| conf={conf:.2f}{staff} | {ts}"
    )


def main():
    args = parse_args()
    events = load_and_sort_events(args.events)

    if not events:
        return

    ingest_url = args.api.rstrip("/")
    print(f"[replay] API: {ingest_url}")
    print(f"[replay] Speed: {args.speed}x | Max gap: {args.max_gap}s")
    print(f"[replay] Starting replay — open the dashboard to see live updates:")
    print(f"[replay]   {ingest_url}/dashboard")
    print(f"[replay] {'─' * 70}")

    # Check API is reachable
    try:
        resp = requests.get(f"{ingest_url}/health", timeout=5)
        if resp.status_code != 200:
            print(f"[replay] WARNING: API health check returned {resp.status_code}")
    except Exception as e:
        print(f"[replay] WARNING: Cannot reach API: {e}")
        print(f"[replay] Make sure the API is running at {ingest_url}")
        return

    start_wall  = time.time()
    first_ts    = None
    accepted    = 0
    rejected    = 0

    for i, event in enumerate(events):
        # Parse event timestamp
        raw_ts = event.get("timestamp", "")
        try:
            evt_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except ValueError:
            evt_ts = None

        # Calculate delay until this event should fire
        if evt_ts and first_ts and args.speed > 0:
            original_gap = (evt_ts - first_ts).total_seconds()
            wait_time    = min(original_gap / args.speed, args.max_gap)
            if wait_time > 0.05:
                time.sleep(wait_time)
        elif first_ts is None and evt_ts:
            first_ts = evt_ts

        # Send the event
        ok = send_event(ingest_url, event)
        elapsed = time.time() - start_wall

        if ok:
            accepted += 1
            print(format_event_line(event, elapsed))
        else:
            rejected += 1
            print(f"  [{elapsed:7.1f}s] REJECTED: {event.get('event_id', '?')[:16]}")

        # Progress every 25 events
        if (i + 1) % 25 == 0:
            print(f"[replay] ── progress: {i+1}/{len(events)} | "
                  f"accepted={accepted} rejected={rejected} ──")

    total_time = time.time() - start_wall
    print(f"[replay] {'─' * 70}")
    print(f"[replay] Done in {total_time:.1f}s")
    print(f"[replay] Accepted: {accepted} | Rejected: {rejected}")
    print(f"[replay] Check metrics: {ingest_url}/stores/ST1008/metrics?date=2026-04-10")
    print(f"[replay] Dashboard:     {ingest_url}/dashboard")


if __name__ == "__main__":
    main()