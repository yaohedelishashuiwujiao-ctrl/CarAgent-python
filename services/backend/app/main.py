import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.routers import agent_jobs, assets, agent_runtime, autohome, collectors, datasets, evidence, import_export, knowledge, metadata, permissions, rag, runtime, vision
from backend.app.services.agent_jobs import get_agent_job_service
from backend.app.observability import metrics_snapshot, trace_metrics_middleware


app = FastAPI(title="Chassis Benchmark Data Platform API", version="0.1.0")
app.middleware("http")(trace_metrics_middleware)

frontend_port = os.getenv("FRONTEND_PORT", "5173")
allowed_origins = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    f"http://localhost:{frontend_port}",
    f"http://127.0.0.1:{frontend_port}",
}

local_dev = settings.app_env == "local"
allow_origins = ["*"] if local_dev else sorted(allowed_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=not local_dev,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(metadata.router, prefix="/api/metadata", tags=["metadata"])
app.include_router(assets.router, prefix="/api/assets", tags=["assets"])
app.include_router(autohome.router, prefix="/api/autohome", tags=["autohome"])
app.include_router(collectors.router, prefix="/api/collectors", tags=["collectors"])
app.include_router(datasets.router, prefix="/api/datasets", tags=["datasets"])
app.include_router(evidence.router, prefix="/api/evidence", tags=["evidence"])
app.include_router(import_export.router, prefix="/api/import-export", tags=["import-export"])
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["knowledge"])
app.include_router(rag.router, prefix="/api/rag", tags=["rag"])
app.include_router(agent_runtime.router, prefix="/api/agent", tags=["agent"])
app.include_router(agent_jobs.router, prefix="/api/agent", tags=["agent-jobs"])
app.include_router(permissions.router, prefix="/api/permissions", tags=["permissions"])
app.include_router(runtime.router, prefix="/api/runtime", tags=["runtime"])
app.include_router(vision.router, prefix="/api/vision", tags=["vision"])


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "chassis-benchmark-platform"}


@app.get("/api/metrics")
def metrics() -> dict:
    return metrics_snapshot()


@app.on_event("startup")
async def start_agent_jobs() -> None:
    await get_agent_job_service().start()


@app.on_event("shutdown")
async def stop_agent_jobs() -> None:
    await get_agent_job_service().stop()
