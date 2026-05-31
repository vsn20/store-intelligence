# pipeline/tracker.py
import argparse
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--detections",    required=True)
    p.add_argument("--store_layout",  required=True)
    p.add_argument("--camera_id",     required=True)
    p.add_argument("--output",        required=True)
    return p.parse_args()


def load_layout(path):
    with open(path) as f:
        return json.load(f)


def point_in_zone(cx_pct, cy_pct, bbox_pct):
    x1, y1, x2, y2 = bbox_pct
    return x1 <= cx_pct <= x2 and y1 <= cy_pct <= y2


def generate_visitor_id():
    return "VIS_" + uuid.uuid4().hex[:6]


def main():
    args = parse_args()
    layout = load_layout(args.store_layout)
    store_id    = layout["store_id"]
    camera_role = layout["cameras"].get(args.camera_id, "unknown")
    camera_zones = [z for z in layout["zones"] if z["camera"] == args.camera_id]

    # State
    track_to_visitor  = {}
    visitor_exited    = {}
    track_zone_entry  = defaultdict(dict)
    track_zone_dwell  = defaultdict(dict)
    session_seq       = defaultdict(int)
    emitted_entries   = set()
    emitted_exits     = set()
    events            = []

    def emit(event_type, visitor_id, camera_id, timestamp, zone_id=None,
             dwell_ms=0, is_staff=False, confidence=0.9, metadata=None):
        session_seq[visitor_id] += 1
        e = {
            "event_id":   str(uuid.uuid4()),
            "store_id":   store_id,
            "camera_id":  camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp":  timestamp if isinstance(timestamp, str) else timestamp.isoformat(),
            "zone_id":    zone_id,
            "dwell_ms":   dwell_ms,
            "is_staff":   is_staff,
            "confidence": round(confidence, 4),
            "metadata": {
                "queue_depth": metadata.get("queue_depth") if metadata else None,
                "sku_zone":    metadata.get("sku_zone") if metadata else None,
                "session_seq": session_seq[visitor_id],
            }
        }
        events.append(e)

    def detect_staff(positions):
        """Track that stays near frame edges >60% of time = staff."""
        if len(positions) < 8:
            return False
        edge_count = sum(1 for _, cy in positions if cy < 0.15 or cy > 0.85)
        return edge_count / len(positions) > 0.6

    # Load detections
    detections = []
    with open(args.detections) as f:
        for line in f:
            line = line.strip()
            if line:
                detections.append(json.loads(line))

    if not detections:
        print(f"[tracker] No detections for {args.camera_id} — writing empty output")
        open(args.output, "w").close()
        return

    print(f"[tracker] Loaded {len(detections)} detections for {args.camera_id} (role: {camera_role})")

    # Group by track_id
    tracks = defaultdict(list)
    for d in detections:
        tracks[d["track_id"]].append(d)

    # For billing: compute real queue depth per timestamp
    # queue_depth = number of unique track_ids active at same time window
    if camera_role == "billing":
        # Build timeline of active tracks
        active_times = {}
        for tid, dets in tracks.items():
            dets.sort(key=lambda d: d["timestamp"])
            active_times[tid] = (dets[0]["timestamp"], dets[-1]["timestamp"])

    for track_id, dets in sorted(tracks.items(), key=lambda x: x[0]):
        dets.sort(key=lambda d: d["timestamp"])

        if track_id not in track_to_visitor:
            track_to_visitor[track_id] = generate_visitor_id()
        visitor_id = track_to_visitor[track_id]

        positions = []
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            cx = ((x1 + x2) / 2) / d["frame_w"]
            cy = ((y1 + y2) / 2) / d["frame_h"]
            positions.append((d["timestamp"], cy))

        is_staff = detect_staff(positions)
        avg_conf = sum(d["confidence"] for d in dets) / len(dets)
        first_ts = dets[0]["timestamp"]
        last_ts  = dets[-1]["timestamp"]

        # ── ENTRY / EXIT camera ──────────────────────────────────
        if camera_role == "entry_exit":
            if len(positions) < 2:
                continue

            cy_values = [cy for _, cy in positions]
            first_cy  = cy_values[0]
            last_cy   = cy_values[-1]
            min_cy    = min(cy_values)
            max_cy    = max(cy_values)
            movement  = last_cy - first_cy
            range_cy  = max_cy - min_cy

            # Lower threshold: any track that spans >15% of frame height
            # AND has a clear direction counts as entry or exit
            if range_cy < 0.10:
                # Barely moved — likely just standing near door, skip
                continue

            if movement > 0.10:   # moving DOWN = entering store
                event_type = "ENTRY"
                if visitor_id in visitor_exited:
                    prev_exit = datetime.fromisoformat(visitor_exited[visitor_id])
                    curr_ts   = datetime.fromisoformat(first_ts)
                    if (curr_ts - prev_exit).total_seconds() < 600:
                        event_type = "REENTRY"

                if track_id not in emitted_entries:
                    emit(event_type, visitor_id, args.camera_id,
                         first_ts, confidence=avg_conf, is_staff=is_staff)
                    emitted_entries.add(track_id)

            elif movement < -0.10:  # moving UP = exiting store
                if track_id not in emitted_exits:
                    emit("EXIT", visitor_id, args.camera_id,
                         last_ts, confidence=avg_conf, is_staff=is_staff)
                    emitted_exits.add(track_id)
                    visitor_exited[visitor_id] = last_ts

            else:
                # Ambiguous direction — use first/last absolute position
                # If track starts near top of frame and ends near bottom = ENTRY
                if first_cy < 0.4 and last_cy > 0.5:
                    if track_id not in emitted_entries:
                        emit("ENTRY", visitor_id, args.camera_id,
                             first_ts, confidence=avg_conf * 0.8, is_staff=is_staff)
                        emitted_entries.add(track_id)
                elif first_cy > 0.5 and last_cy < 0.4:
                    if track_id not in emitted_exits:
                        emit("EXIT", visitor_id, args.camera_id,
                             last_ts, confidence=avg_conf * 0.8, is_staff=is_staff)
                        emitted_exits.add(track_id)
                        visitor_exited[visitor_id] = last_ts

        # ── FLOOR / ZONE camera ──────────────────────────────────
        elif camera_role in ("main_floor", "floor_secondary"):
            if not camera_zones:
                continue

            for d in dets:
                x1, y1, x2, y2 = d["bbox"]
                cx_pct = ((x1 + x2) / 2) / d["frame_w"]
                cy_pct = ((y1 + y2) / 2) / d["frame_h"]
                ts     = d["timestamp"]

                for zone in camera_zones:
                    zid = zone["zone_id"]
                    if point_in_zone(cx_pct, cy_pct, zone["bbox_pct"]):
                        # Zone enter
                        if zid not in track_zone_entry[track_id]:
                            track_zone_entry[track_id][zid] = ts
                            emit("ZONE_ENTER", visitor_id, args.camera_id, ts,
                                 zone_id=zid, is_staff=is_staff,
                                 confidence=d["confidence"],
                                 metadata={"sku_zone": zone.get("sku_zone")})

                        # Zone dwell every 30s
                        entry_ts  = datetime.fromisoformat(track_zone_entry[track_id][zid])
                        curr_ts   = datetime.fromisoformat(ts)
                        elapsed   = (curr_ts - entry_ts).total_seconds()
                        last_dwell = track_zone_dwell[track_id].get(zid)

                        if elapsed >= 30:
                            last_dwell_ts = datetime.fromisoformat(last_dwell) if last_dwell else entry_ts
                            if (curr_ts - last_dwell_ts).total_seconds() >= 30:
                                dwell_ms = int(elapsed * 1000)
                                emit("ZONE_DWELL", visitor_id, args.camera_id, ts,
                                     zone_id=zid, dwell_ms=dwell_ms, is_staff=is_staff,
                                     confidence=d["confidence"],
                                     metadata={"sku_zone": zone.get("sku_zone")})
                                track_zone_dwell[track_id][zid] = ts
                    else:
                        # Zone exit
                        if zid in track_zone_entry[track_id]:
                            entry_ts  = datetime.fromisoformat(track_zone_entry[track_id].pop(zid))
                            curr_ts   = datetime.fromisoformat(ts)
                            dwell_ms  = int((curr_ts - entry_ts).total_seconds() * 1000)
                            emit("ZONE_EXIT", visitor_id, args.camera_id, ts,
                                 zone_id=zid, dwell_ms=dwell_ms, is_staff=is_staff,
                                 confidence=d["confidence"],
                                 metadata={"sku_zone": zone.get("sku_zone")})

        # ── BILLING camera ───────────────────────────────────────
        elif camera_role == "billing":
            if not dets:
                continue

            # Real queue depth: count tracks active at same time as this track
            my_start = first_ts
            my_end   = last_ts
            concurrent = sum(
                1 for tid2, (t_start, t_end) in active_times.items()
                if tid2 != track_id and t_start <= my_end and t_end >= my_start
            )
            queue_depth = max(1, concurrent)

            emit("BILLING_QUEUE_JOIN", visitor_id, args.camera_id, first_ts,
                 zone_id="BILLING", is_staff=is_staff, confidence=avg_conf,
                 metadata={"queue_depth": queue_depth, "sku_zone": "billing"})

            # Abandonment: track present < 60 seconds = likely abandoned
            try:
                start_dt = datetime.fromisoformat(first_ts)
                end_dt   = datetime.fromisoformat(last_ts)
                duration = (end_dt - start_dt).total_seconds()
                if duration < 60 and not is_staff:
                    emit("BILLING_QUEUE_ABANDON", visitor_id, args.camera_id, last_ts,
                         zone_id="BILLING", dwell_ms=int(duration * 1000),
                         is_staff=is_staff, confidence=avg_conf * 0.85,
                         metadata={"queue_depth": queue_depth, "sku_zone": "billing"})
            except Exception:
                pass

    # Write output
    with open(args.output, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    # Summary
    from collections import Counter
    type_counts = Counter(e["event_type"] for e in events)
    print(f"[tracker] Done. Emitted {len(events)} events → {args.output}")
    for k, v in sorted(type_counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()