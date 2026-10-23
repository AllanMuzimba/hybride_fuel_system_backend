"""
Fuel Management System API
Run with: uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Interactive docs (great for wiring up Flutter): http://localhost:8000/docs
"""
import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import os

import httpx

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .database import engine

from .routers import (
    auth_router,
    companies_router,
    users_router,
    suppliers_router,
    fuel_types_router,
    receipts_router,
    customers_router,
    sales_router,
    shifts_router,
    expenses_router,
    reports_router,
)

logger = logging.getLogger(__name__)


async def keep_alive_loop(public_url: str) -> None:
    """Keep the Render API and its Layerbase connection active while running."""
    health_url = f"{public_url.rstrip('/')}/health?check_database=true"
    await asyncio.sleep(60)
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                response = await client.get(health_url)
                response.raise_for_status()
                logger.info("Keepalive health check succeeded")
            except (httpx.HTTPError, httpx.InvalidURL):
                logger.warning("Keepalive health check failed", exc_info=True)
            await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    public_url = os.getenv("RENDER_EXTERNAL_URL", "")
    task = None
    if public_url.startswith("https://"):
        task = asyncio.create_task(keep_alive_loop(public_url))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Fuel Management System API",
    description="Backend for supplier receipts, fuel sales, customer accounts and reporting.",
    version="1.0.0",
    lifespan=lifespan,
)

# Set CORS_ORIGINS to the deployed Flutter site's origin. Native mobile apps
# do not use browser CORS. The wildcard is retained for local development.
cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "*").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(companies_router.router)
app.include_router(users_router.router)
app.include_router(suppliers_router.router)
app.include_router(fuel_types_router.router)
app.include_router(receipts_router.router)
app.include_router(customers_router.router)
app.include_router(sales_router.router)
app.include_router(shifts_router.router)
app.include_router(expenses_router.router)
app.include_router(reports_router.router)


@app.get("/health", tags=["health"])
def health_check(check_database: bool = False):
    if check_database:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok"}


@app.get("/ping", tags=["health"])
def ping():
    return {"status": "alive"}
