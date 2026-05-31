from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from app.database import init_db
import os


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Purplle Store Intelligence API", lifespan=lifespan)

# ── Routers ───────────────────────────────────────────────────────────────────
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
    app.mount("/dashboard", StaticFiles(directory=dashboard_path, html=True), name="dashboard")


@app.get("/")
def root():
    return {"message": "Purplle Store Intelligence API", "docs": "/docs"}