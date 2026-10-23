"""
Fuel Management System API
Run with: uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

Interactive docs (great for wiring up Flutter): http://localhost:8000/docs
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app = FastAPI(
    title="Fuel Management System API",
    description="Backend for supplier receipts, fuel sales, customer accounts and reporting.",
    version="1.0.0",
)

# Allow the Flutter app (mobile/web/desktop) to call this API.
# Lock this down to your actual app origin(s) in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
def health_check():
    return {"status": "ok"}
