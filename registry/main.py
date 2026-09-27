from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from registry.auth import require_read_access
from registry.database import run_migrations
from registry.routers import contracts, health, results


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_migrations()
    yield


app = FastAPI(
    title="Akad Contract Registry",
    description="Contract storage, versioning, and breach history for the Akad data contract framework",
    version="1.0.0",
    lifespan=lifespan,
)

_read_access = [Depends(require_read_access)]  # no-op unless reads require auth

app.include_router(contracts.router, prefix="/contracts",          tags=["Contracts"],          dependencies=_read_access)
app.include_router(results.router,   prefix="/validation-results", tags=["Validation Results"], dependencies=_read_access)
app.include_router(health.router,    prefix="/health",             tags=["Health"])
