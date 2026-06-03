# app/stream.py
# Server-Sent Events endpoint — streams live events to the dashboard in real time
# Mount this router in main.py: app.include_router(stream.router)

import asyncio
import json
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.database import get_db
from app.models import EventORM

router = APIRouter()

# Global store: set of queues, one per connected SSE client
_clients: set = set()


async def _event_generator(request: Request, store_id: str, db: Session):
    """
    Streams new events to the client as Server-Sent Events.
    Polls the DB every second for events newer than the last seen timestamp.
    Sends a heartbeat every 5 seconds to keep the connection alive.
    """
    queue: asyncio.Queue = asyncio.Queue()
    _clients.add(queue)

    # Start from events in the last 60 seconds so the dashboard
    # immediately has context on connect
    since = datetime.now(timezone.utc) - timedelta(seconds=60)
    last_id = None

    try:
        heartbeat_counter = 0
        while True:
            # Check client disconnect
            if await request.is_disconnected():
                break

            # Query new events since last seen
            query = db.query(EventORM).filter(
                EventORM.store_id == store_id,
                EventORM.timestamp > since,
            ).order_by(EventORM.timestamp.asc(), EventORM.id.asc())

            if last_id:
                query = query.filter(EventORM.id > last_id)

            new_events = query.limit(50).all()

            for evt in new_events:
                last_id = evt.id
                ts = evt.timestamp
                if hasattr(ts, "isoformat"):
                    ts_str = ts.isoformat()
                else:
                    ts_str = str(ts)

                payload = {
                    "event_id":   evt.event_id,
                    "store_id":   evt.store_id,
                    "event_type": evt.event_type,
                    "visitor_id": evt.visitor_id,
                    "zone_id":    evt.zone_id,
                    "is_staff":   evt.is_staff,
                    "confidence": float(evt.confidence) if evt.confidence else None,
                    "timestamp":  ts_str,
                    "metadata":   evt.metadata_ or {},
                }
                data = json.dumps(payload)
                yield f"event: store_event\ndata: {data}\n\n"
                since = ts

            # Heartbeat every ~5 seconds (5 x 1s sleep)
            heartbeat_counter += 1
            if heartbeat_counter >= 5:
                heartbeat_counter = 0
                now = datetime.now(timezone.utc).isoformat()
                yield f"event: heartbeat\ndata: {json.dumps({'ts': now})}\n\n"

            await asyncio.sleep(1)

    finally:
        _clients.discard(queue)


@router.get("/stores/{store_id}/stream")
async def stream_events(store_id: str, request: Request, db: Session = Depends(get_db)):
    """
    SSE endpoint. Connect with:
        const es = new EventSource('/stores/ST1008/stream');
        es.addEventListener('store_event', e => console.log(JSON.parse(e.data)));
    """
    return StreamingResponse(
        _event_generator(request, store_id, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",     # disable nginx buffering
            "Access-Control-Allow-Origin": "*",
        },
    )