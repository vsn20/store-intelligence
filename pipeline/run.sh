#!/bin/bash
set -e

STORE_ID="ST1008"
CLIPS_DIR="../data/clips"
OUTPUT_DIR="../output"
LAYOUT="../data/store_layout.json"
CLIP_DATE="2026-04-10"

mkdir -p "$OUTPUT_DIR"
> "$OUTPUT_DIR/events.jsonl"   # clear output

declare -A CAM_TIMES=(
  ["CAM 1"]="${CLIP_DATE}T12:00:00+00:00"
  ["CAM 2"]="${CLIP_DATE}T12:00:00+00:00"
  ["CAM 3"]="${CLIP_DATE}T12:00:00+00:00"
  ["CAM 4"]="${CLIP_DATE}T12:00:00+00:00"
  ["CAM 5"]="${CLIP_DATE}T12:00:00+00:00"
)

for cam in "CAM 1" "CAM 2" "CAM 3" "CAM 4" "CAM 5"; do
  VIDEO="$CLIPS_DIR/${cam}.mp4"
  if [ ! -f "$VIDEO" ]; then
    echo "⚠ Skipping $cam — file not found: $VIDEO"
    continue
  fi

  SAFE_CAM="${cam// /_}"
  DETECT_OUT="$OUTPUT_DIR/detections_${SAFE_CAM}.jsonl"
  EVENTS_OUT="$OUTPUT_DIR/events_${SAFE_CAM}.jsonl"

  echo "🎥 Processing $cam..."
  python detect.py \
    --video "$VIDEO" \
    --store_id "$STORE_ID" \
    --camera_id "$cam" \
    --clip_start_iso "${CAM_TIMES[$cam]}" \
    --output "$DETECT_OUT"

  python tracker.py \
    --detections "$DETECT_OUT" \
    --store_layout "$LAYOUT" \
    --camera_id "$cam" \
    --output "$EVENTS_OUT"

  cat "$EVENTS_OUT" >> "$OUTPUT_DIR/events.jsonl"
  echo "✅ $cam done"
done

echo ""
echo "=== PIPELINE SUMMARY ==="
echo "Total events: $(wc -l < $OUTPUT_DIR/events.jsonl)"
echo "Events per type:"
python3 -c "
import json
from collections import Counter
c = Counter()
with open('$OUTPUT_DIR/events.jsonl') as f:
    for line in f:
        e = json.loads(line)
        c[e['event_type']] += 1
for k,v in sorted(c.items()):
    print(f'  {k}: {v}')
"
echo ""
echo "Output: $OUTPUT_DIR/events.jsonl"