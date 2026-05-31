import time
import uuid
import json
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from app.database import init_db
import os

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"%(extra_fields)s}',
)


class ExtraFieldsFilter(logging.Filter):
    """Inject extra_fields JSON fragment so the formatter above works cleanly."""
    def filter(self, record):
        extra_keys = {
            k: v for k, v in record.__dict__.items()
            if k not in logging.LogRecord.__dict__ and
               k not in ("args", "msg", "levelname", "levelno", "pathname",
                         "filename", "module", "exc_info", "exc_text",
                         "stack_info", "lineno", "funcName", "created",
                         "msecs", "relativeCreated", "thread", "threadName",
                         "processName", "process", "name", "message",
                         "asctime", "taskName")
        }
        if extra_keys:
            record.extra_fields = "," + ",".join(
                f'"{k}":{json.dumps(v)}' for k, v in extra_keys.items()
            )
        else:
            record.extra_fields = ""
        return True


root_logger = logging.getLogger()
for handler in root_logger.handlers:
    handler.addFilter(ExtraFieldsFilter())

logger = logging.getLogger("purplle.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Purplle Store Intelligence API", lifespan=lifespan)


# ── Request logging middleware ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    start = time.time()

    # Extract store_id from path if present (/stores/{store_id}/...)
    path_parts = request.url.path.split("/")
    store_id = "unknown"
    if len(path_parts) > 2 and path_parts[1] == "stores":
        store_id = path_parts[2]

    try:
        response = await call_next(request)
    except Exception as exc:
        latency_ms = int((time.time() - start) * 1000)
        logger.error(
            "unhandled_exception",
            extra=dict(
                trace_id=trace_id,
                store_id=store_id,
                endpoint=request.url.path,
                method=request.method,
                latency_ms=latency_ms,
                status_code=500,
                error=str(exc),
            ),
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_server_error", "trace_id": trace_id},
        )

    latency_ms = int((time.time() - start) * 1000)
    logger.info(
        "request",
        extra=dict(
            trace_id=trace_id,
            store_id=store_id,
            endpoint=request.url.path,
            method=request.method,
            latency_ms=latency_ms,
            status_code=response.status_code,
        ),
    )
    return response


# ── Global exception handler — no raw stack traces in responses ───────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    logger.exception(
        "unhandled_exception",
        extra=dict(
            trace_id=trace_id,
            endpoint=request.url.path,
            error=str(exc),
        ),
    )
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "trace_id": trace_id},
    )


# ── Routers ───────────────────────────────────────────────────────────────────
# NOTE: file on disk is named ingenstion.py (typo preserved to avoid rename
# breaking the existing repo); import alias uses the correct spelling.
from app.ingenstion import router as ingest_router
from app.metrics    import router as metrics_router
from app.funnel     import router as funnel_router
from app.anomalies  import router as anomalies_router
from app.health     import router as health_router
from app.heatmap    import router as heatmap_router

app.include_router(ingest_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(anomalies_router)
app.include_router(health_router)
app.include_router(heatmap_router)

# ── Static dashboard ──────────────────────────────────────────────────────────
dashboard_path = os.path.join(os.path.dirname(__file__), "..", "dashboard")
if os.path.exists(dashboard_path):
    app.mount(
        "/dashboard",
        StaticFiles(directory=dashboard_path, html=True),
        name="dashboard",
    )


@app.get("/")
def root():
    return {"message": "Purplle Store Intelligence API", "docs": "/docs"}