# pipeline/detect.py
import argparse
import json
import uuid
import cv2
from datetime import datetime, timezone, timedelta
from ultralytics import YOLO

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--video",           required=True)
    p.add_argument("--store_id",        required=True)
    p.add_argument("--camera_id",       required=True)
    p.add_argument("--clip_start_iso",  required=True)
    p.add_argument("--output",          required=True)
    p.add_argument("--skip_frames",     type=int, default=3)
    p.add_argument("--conf_threshold",  type=float, default=0.35)
    return p.parse_args()


def main():
    args = parse_args()

    model = YOLO("yolov8m.pt")  # downloads automatically on first run
    clip_start = datetime.fromisoformat(args.clip_start_iso.replace("Z", "+00:00"))

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {args.video}")

    fps          = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"[detect] {args.camera_id} | fps={fps:.1f} | total_frames={total_frames}")

    if total_frames == 0:
        print(f"[detect] WARNING: video reports 0 frames — may be corrupt: {args.video}")
        cap.release()
        open(args.output, "w").close()
        return

    frame_num  = 0
    written    = 0

    with open(args.output, "w") as out_f:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_num += 1
            if frame_num % args.skip_frames != 0:
                continue

            try:
                results = model.track(
                    frame,
                    persist=True,
                    classes=[0],          # person only
                    conf=args.conf_threshold,
                    verbose=False,
                    iou=0.5,
                )
            except Exception as e:
                print(f"[detect] frame {frame_num} error: {e}")
                continue

            frame_time = clip_start + timedelta(seconds=frame_num / fps)
            frame_h, frame_w = frame.shape[:2]

            if results and results[0].boxes is not None:
                boxes = results[0].boxes
                for i in range(len(boxes)):
                    try:
                        track_id   = int(boxes.id[i]) if boxes.id is not None else -1
                        conf       = float(boxes.conf[i])
                        x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                        record = {
                            "frame":      frame_num,
                            "track_id":   track_id,
                            "bbox":       [x1, y1, x2, y2],
                            "confidence": round(conf, 4),
                            "timestamp":  frame_time.isoformat(),
                            "frame_w":    frame_w,
                            "frame_h":    frame_h,
                            "store_id":   args.store_id,
                            "camera_id":  args.camera_id,
                        }
                        out_f.write(json.dumps(record) + "\n")
                        written += 1
                    except Exception as e:
                        continue

            if frame_num % 150 == 0:
                print(f"[detect] frame {frame_num}/{total_frames} | detections so far: {written}")

    cap.release()
    print(f"[detect] Done. Total detections written: {written}")


if __name__ == "__main__":
    main()