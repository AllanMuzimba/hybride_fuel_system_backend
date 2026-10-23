"""
Fuel Management System API
Run with: uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Interactive docs (great for wiring up Flutter): http://localhost:8000/docs
"""
import os

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
    billing_router,
    kiosk_router,
)

app = FastAPI(
    title="Fuel Management System API",
    description="Backend for supplier receipts, fuel sales, customer accounts and reporting.",
    version="1.0.0",
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
app.include_router(billing_router.router)
app.include_router(kiosk_router.router)


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
