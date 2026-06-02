# pipeline/ingest.py
# Reads events.jsonl and POSTs them to the API in batches of 250

import json
import sys
import requests
from pathlib import Path

EVENTS_FILE = "../output/events.jsonl"
API_URL = "http://localhost:8000/events/ingest"
BATCH_SIZE  = 250


def main():
    events = []
    with open(EVENTS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    if not events:
        print("No events found in", EVENTS_FILE)
        sys.exit(1)

    print(f"Loaded {len(events)} events. Ingesting in batches of {BATCH_SIZE}...")

    total_accepted = 0
    total_rejected = 0

    for i in range(0, len(events), BATCH_SIZE):
        batch = events[i : i + BATCH_SIZE]
        payload = {"events": batch}

        try:
            resp = requests.post(API_URL, json=payload, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                total_accepted += result.get("accepted", 0)
                total_rejected += result.get("rejected", 0)
                print(f"  Batch {i//BATCH_SIZE + 1}: accepted={result['accepted']} rejected={result['rejected']}")
                if result.get("errors"):
                    for err in result["errors"][:3]:
                        print(f"    ERROR: {err}")
            else:
                print(f"  Batch {i//BATCH_SIZE + 1}: HTTP {resp.status_code} — {resp.text[:200]}")
        except Exception as e:
            print(f"  Batch {i//BATCH_SIZE + 1}: FAILED — {e}")

    print(f"\nDone. Total accepted: {total_accepted} | Total rejected: {total_rejected}")
    print(f"Check metrics: http://localhost:8000/stores/ST1008/metrics?date=2026-04-10")


if __name__ == "__main__":
    main()
