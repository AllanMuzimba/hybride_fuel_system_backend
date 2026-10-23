from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db
from .shifts_router import require_on_shift, require_realtime_shift

router = APIRouter(prefix="/kiosk", tags=["kiosk"])
DEFAULT_ROLES = ["manager", "supervisor", "developer"]


def _settings(db, company_id):
    row = db.query(models.CompanySalesSettings).get(company_id)
    return row or models.CompanySalesSettings(
        company_id=company_id, kiosk_enabled=True,
        kiosk_sell_roles=DEFAULT_ROLES, kiosk_view_roles=DEFAULT_ROLES)


def _permit(db, user, action):
    config = _settings(db, user.company_id)
    roles = (config.kiosk_sell_roles if action == "sell"
             else list(set(config.kiosk_view_roles + config.kiosk_sell_roles)))
    if not config.kiosk_enabled or user.role not in roles:
        raise HTTPException(403, "Kiosk module or permission is disabled for your role")
    return config


@router.get("/settings")
def get_settings(company_id: Optional[int] = None, db: Session = Depends(get_db),
                 user: models.User = Depends(auth.get_current_user)):
    target = auth.resolve_company_id(db, user, company_id)
    row = _settings(db, target)
    return {"company_id": target, "kiosk_enabled": row.kiosk_enabled,
            "kiosk_sell_roles": row.kiosk_sell_roles, "kiosk_view_roles": row.kiosk_view_roles}


@router.patch("/settings")
def update_settings(payload: schemas.CompanySalesSettingsUpdate, db: Session = Depends(get_db),
                    user: models.User = Depends(auth.require_role("developer"))):
    auth.resolve_company_id(db, user, payload.company_id)
    row = db.query(models.CompanySalesSettings).get(payload.company_id)
    if row is None:
        row = models.CompanySalesSettings(company_id=payload.company_id)
        db.add(row)
    row.kiosk_enabled = payload.kiosk_enabled
    row.kiosk_sell_roles = list(dict.fromkeys(payload.kiosk_sell_roles))
    row.kiosk_view_roles = list(dict.fromkeys(payload.kiosk_view_roles))
    row.updated_by = user.id
    row.updated_at = func.now()
    db.commit()
    return get_settings(payload.company_id, db, user)


@router.get("/categories")
def categories(db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    _permit(db, user, "view")
    rows = db.query(models.ProductCategory).filter_by(company_id=user.company_id).order_by(models.ProductCategory.name).all()
    return [{"id": r.id, "name": r.name, "is_active": r.is_active} for r in rows]


@router.post("/categories")
def create_category(payload: schemas.ProductCategoryCreate, db: Session = Depends(get_db),
                    user: models.User = Depends(auth.get_current_user)):
    _permit(db, user, "view")
    row = models.ProductCategory(company_id=user.company_id, name=payload.name.strip())
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(400, "Category already exists") from exc
    return {"id": row.id, "name": row.name, "is_active": row.is_active}


def _product(row, category):
    return {"id": row.id, "branch_id": row.branch_id, "category_id": row.category_id,
            "category_name": category, "name": row.name, "cost_price": float(row.cost_price),
            "sell_price": float(row.sell_price), "stock_quantity": float(row.stock_quantity),
            "is_active": row.is_active}


@router.get("/products")
def products(branch_id: Optional[int] = None, active_only: bool = False,
             db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    _permit(db, user, "view")
    q = db.query(models.KioskProduct, models.ProductCategory.name).join(
        models.ProductCategory).filter(models.KioskProduct.company_id == user.company_id)
    target = branch_id or user.branch_id
    if target:
        auth.verify_branch_access(user, target, db)
        q = q.filter(models.KioskProduct.branch_id == target)
    if active_only:
        q = q.filter(models.KioskProduct.is_active.is_(True))
    return [_product(row, category) for row, category in q.order_by(models.KioskProduct.name).all()]


@router.post("/products")
def create_product(payload: schemas.KioskProductCreate, db: Session = Depends(get_db),
                   user: models.User = Depends(auth.get_current_user)):
    _permit(db, user, "view")
    auth.verify_branch_access(user, payload.branch_id, db)
    category = db.query(models.ProductCategory).filter_by(
        id=payload.category_id, company_id=user.company_id).first()
    if not category:
        raise HTTPException(404, "Category not found")
    row = models.KioskProduct(company_id=user.company_id, **payload.model_dump())
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(400, "Product name already exists at this branch") from exc
    return _product(row, category.name)


@router.patch("/products/{product_id}")
def update_product(product_id: int, payload: schemas.KioskProductUpdate,
                   db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    _permit(db, user, "view")
    row = db.query(models.KioskProduct).filter_by(id=product_id, company_id=user.company_id).first()
    if not row:
        raise HTTPException(404, "Product not found")
    auth.verify_branch_access(user, row.branch_id, db)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = func.now()
    db.commit()
    category = db.query(models.ProductCategory).get(row.category_id)
    return _product(row, category.name)


@router.post("/sales")
def checkout(payload: schemas.KioskSaleCreate, db: Session = Depends(get_db),
             user: models.User = Depends(auth.get_current_user)):
    _permit(db, user, "sell")
    auth.verify_branch_access(user, payload.branch_id, db)
    if payload.payment_method == "account":
        raise HTTPException(400, "Account payment is unavailable for mixed sales")
    has_fuel = any(item.fuel_type_id for item in payload.items)
    shift = None
    if has_fuel:
        shift = require_realtime_shift(db, payload.branch_id)
        require_on_shift(db, shift, user)
    prepared, total = [], Decimal("0")
    for item in payload.items:
        quantity = Decimal(str(item.quantity))
        if item.product_id:
            source = db.query(models.KioskProduct).filter_by(
                id=item.product_id, company_id=user.company_id,
                branch_id=payload.branch_id, is_active=True).with_for_update().first()
            if not source or source.stock_quantity < quantity:
                raise HTTPException(400, "Product is unavailable or has insufficient stock")
            kind, cost, price = "product", source.cost_price, source.sell_price
        else:
            source = db.query(models.FuelType).filter_by(
                id=item.fuel_type_id, company_id=user.company_id).first()
            branch_price = db.query(models.BranchFuelPrice).get((payload.branch_id, item.fuel_type_id))
            if not source or not branch_price:
                raise HTTPException(404, "Fuel type or price not found")
            kind, cost, price = "fuel", branch_price.current_cost, branch_price.sell_price
        amount = quantity * price
        total += amount
        prepared.append((kind, source, quantity, cost, price, amount))
    cash = Decimal(str(payload.cash_received or 0))
    if payload.payment_method == "cash" and cash < total:
        raise HTTPException(400, "Cash received is less than the sale total")
    order = models.KioskSale(company_id=user.company_id, branch_id=payload.branch_id,
        shift_id=shift.id if shift else None, payment_method=payload.payment_method,
        total_amount=total, cash_received=cash if payload.payment_method == "cash" else None,
        change_given=cash-total if payload.payment_method == "cash" else None, served_by=user.id)
    db.add(order)
    db.flush()
    for kind, source, quantity, cost, price, amount in prepared:
        fuel_sale = None
        if kind == "product":
            source.stock_quantity -= quantity
        else:
            fuel_sale = models.Sale(branch_id=payload.branch_id, fuel_type_id=source.id,
                shift_id=shift.id, volume=quantity, cost_price=cost, sell_price=price,
                payment_method=payload.payment_method, served_by=user.id,
                entry_source="realtime", notes="Kiosk order " + str(order.id))
            db.add(fuel_sale)
            db.flush()
        db.add(models.KioskSaleItem(sale_id=order.id,
            product_id=source.id if kind == "product" else None,
            fuel_sale_id=fuel_sale.id if fuel_sale else None, item_name=source.name,
            item_type=kind, quantity=quantity, unit_cost=cost,
            unit_price=price, total_amount=amount))
    try:
        db.commit()
        db.refresh(order)
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(400, str(exc.orig)) from exc
    return {"id": order.id, "total_amount": float(total),
            "change_given": float(max(cash-total, 0)), "sale_date": order.sale_date}


@router.get("/sales")
def sales(start_date: Optional[date] = None, end_date: Optional[date] = None,
          branch_id: Optional[int] = None, db: Session = Depends(get_db),
          user: models.User = Depends(auth.get_current_user)):
    _permit(db, user, "view")
    q = db.query(models.KioskSale, models.CompanyBranch.branch_name).join(
        models.CompanyBranch).filter(models.KioskSale.company_id == user.company_id)
    if branch_id:
        auth.verify_branch_access(user, branch_id, db)
        q = q.filter(models.KioskSale.branch_id == branch_id)
    if start_date:
        q = q.filter(models.KioskSale.sale_date >= datetime.combine(start_date, time.min))
    if end_date:
        q = q.filter(models.KioskSale.sale_date < datetime.combine(end_date + timedelta(days=1), time.min))
    output = []
    for sale, branch in q.order_by(models.KioskSale.sale_date.desc()).all():
        items = db.query(models.KioskSaleItem).filter_by(sale_id=sale.id).all()
        output.append({"id": sale.id, "branch_name": branch, "payment_method": sale.payment_method,
          "total_amount": float(sale.total_amount), "sale_date": sale.sale_date,
          "items": [{"item_name": i.item_name, "item_type": i.item_type,
          "quantity": float(i.quantity), "unit_price": float(i.unit_price),
          "total_amount": float(i.total_amount)} for i in items]})
    return output


@router.get("/reports/daily")
def daily(start_date: date, end_date: date, branch_id: Optional[int] = None,
          db: Session = Depends(get_db), user: models.User = Depends(auth.get_current_user)):
    _permit(db, user, "view")
    q = db.query(func.date(models.KioskSale.sale_date), models.CompanyBranch.branch_name,
        func.count(models.KioskSale.id), func.sum(models.KioskSale.total_amount)).join(
        models.CompanyBranch).filter(models.KioskSale.company_id == user.company_id,
        models.KioskSale.sale_date >= datetime.combine(start_date, time.min),
        models.KioskSale.sale_date < datetime.combine(end_date + timedelta(days=1), time.min))
    if branch_id:
        auth.verify_branch_access(user, branch_id, db)
        q = q.filter(models.KioskSale.branch_id == branch_id)
    return [{"sale_date": str(day), "branch_name": branch, "transactions": count,
             "sales_total": float(total or 0)} for day, branch, count, total in
            q.group_by(func.date(models.KioskSale.sale_date), models.CompanyBranch.branch_name).all()]


@router.get("/reports/stock")
def stock(branch_id: Optional[int] = None, db: Session = Depends(get_db),
          user: models.User = Depends(auth.get_current_user)):
    return products(branch_id, False, db, user)
