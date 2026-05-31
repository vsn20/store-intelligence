from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()   # creates tables on startup
    yield


app = FastAPI(title="Purplle Store Intelligence API", lifespan=lifespan)


# ── Import routers (we'll fill these files next) ──────────────────────────────
from app.ingenstion import router as ingest_router   # noqa: E402
from app.metrics    import router as metrics_router  # noqa: E402
from app.funnel     import router as funnel_router   # noqa: E402
from app.anomalies  import router as anomalies_router# noqa: E402
from app.health     import router as health_router   # noqa: E402

app.include_router(ingest_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(anomalies_router)
app.include_router(health_router)


@app.get("/")
def root():
    return {"message": "Purplle Store Intelligence API", "docs": "/docs"}