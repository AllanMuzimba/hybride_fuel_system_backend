from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db
from .shifts_router import require_on_shift, require_realtime_shift

router = APIRouter(prefix="/sales", tags=["sales"])


def _shift_stamps(db: Session, shift_ids) -> Dict[int, Tuple[str, str, object]]:
    """shift_id -> ('Morning', '06:00-14:00', date). Every sale carries the
    period stamp of the shift it was booked to, so a flat list of sales still
    shows which shift each row belongs to."""
    wanted = {i for i in shift_ids if i is not None}
    if not wanted:
        return {}
    out: Dict[int, Tuple[str, str, object]] = {}
    for s in db.query(models.Shift).filter(models.Shift.id.in_(wanted)):
        out[s.id] = (
            s.shift_label,
            f"{s.start_time:%H:%M}-{s.end_time:%H:%M}",
            s.shift_date,
        )
    return out


def _sale_out(sale: models.Sale, stamps: Dict[int, Tuple[str, str, object]]) -> schemas.SaleOut:
    out = schemas.SaleOut.model_validate(sale, from_attributes=True)
    stamp = stamps.get(sale.shift_id) if sale.shift_id else None
    if stamp:
        out.shift_label, out.shift_period, out.shift_date = stamp
    return out


@router.post("", response_model=schemas.SaleOut)
def record_sale(
    payload: schemas.SaleCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("attendant", "supervisor", "manager", "developer")),
):
    """Record a fuel sale at one of your company's branches. Cost/sell price
    are snapshotted from THAT BRANCH's own current price (branch_fuel_prices)
    — not a company-wide price, since branches can charge differently. Stock
    is decremented and (for 'account' sales) the customer's balance is
    updated automatically via database triggers, which also enforce the
    customer's monthly credit limit and confirm everything belongs to the
    same company as the branch.

    The sale is booked to the branch's OPEN shift and the seller must have
    signed in for it — no sales outside a shift period. Shifts opened in
    'end_of_shift' mode don't take live sales at all; their volume is typed
    in later on the Shifts page (POST /shifts/{id}/volume-entries)."""
    auth.verify_branch_access(current_user, payload.branch_id, db)
    if payload.client_request_id:
        existing = db.query(models.Sale).filter(
            models.Sale.client_request_id == payload.client_request_id
        ).first()
        if existing:
            if (existing.served_by != current_user.id or existing.branch_id != payload.branch_id
                    or existing.fuel_type_id != payload.fuel_type_id
                    or existing.customer_id != payload.customer_id
                    or existing.payment_method != payload.payment_method
                    or existing.volume != Decimal(str(payload.volume))
                    or (payload.shift_id is not None and existing.shift_id != payload.shift_id)):
                raise HTTPException(status_code=409, detail="Sale request ID belongs to another sale")
            return _sale_out(existing, _shift_stamps(db, [existing.shift_id]))

    if payload.shift_id is not None:
        shift = db.query(models.Shift).filter(
            models.Shift.id == payload.shift_id,
            models.Shift.branch_id == payload.branch_id,
            models.Shift.company_id == current_user.company_id,
            models.Shift.entry_mode == "realtime",
        ).first()
        if not shift:
            raise HTTPException(status_code=409, detail="Original sales shift is unavailable")
        if payload.recorded_at is None or payload.recorded_at.tzinfo is None:
            raise HTTPException(status_code=422, detail="Offline sale needs a timezone-aware recorded_at")
        recorded_at = payload.recorded_at.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if recorded_at > now + timedelta(minutes=5):
            raise HTTPException(status_code=409, detail="Offline sale time is outside the allowed window")
        if recorded_at < shift.opened_at or (shift.closed_at and recorded_at > shift.closed_at):
            raise HTTPException(status_code=409, detail="Offline sale was outside this shift")
        attendance = db.query(models.ShiftAttendance).filter(
            models.ShiftAttendance.shift_id == shift.id,
            models.ShiftAttendance.user_id == current_user.id,
            models.ShiftAttendance.joined_at <= recorded_at,
        ).first()
        if not attendance or (attendance.left_at and recorded_at > attendance.left_at):
            raise HTTPException(status_code=409, detail="Seller was not signed in at the sale time")
    else:
        shift = require_realtime_shift(db, payload.branch_id)
        require_on_shift(db, shift, current_user)
        recorded_at = None

    fuel_type = db.query(models.FuelType).get(payload.fuel_type_id)
    if not fuel_type or fuel_type.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Fuel type not found in your company")

    pump = None
    if payload.pump_id is not None:
        pump = db.query(models.FuelPump).get(payload.pump_id)
        tank = db.query(models.FuelTank).get(pump.tank_id) if pump else None
        if (not pump or not pump.is_active or pump.branch_id != payload.branch_id
                or not tank or tank.fuel_type_id != payload.fuel_type_id):
            raise HTTPException(status_code=400, detail="Pump does not match the selected branch and fuel")
        if current_user.role == "attendant" and not db.query(models.ShiftPumpAssignment).filter_by(
            shift_id=shift.id, pump_id=pump.id, user_id=current_user.id
        ).first():
            raise HTTPException(status_code=403, detail="This pump is not assigned to you for the active shift")
    elif db.query(models.FuelPump).filter(
        models.FuelPump.branch_id == payload.branch_id,
        models.FuelPump.is_active.is_(True),
    ).first():
        raise HTTPException(status_code=400, detail="Select an assigned pump for this sale")

    price = db.query(models.BranchFuelPrice).get((payload.branch_id, payload.fuel_type_id))
    if not price:
        raise HTTPException(
            status_code=400,
            detail="This branch has no price set for that fuel type yet — set one via PATCH /fuel-types/branch-prices",
        )

    if payload.payment_method == "account" and not payload.customer_id:
        raise HTTPException(status_code=400, detail="customer_id is required for account sales")
    if payload.payment_method == "account":
        customer = db.query(models.Customer).filter(
            models.Customer.id == payload.customer_id,
            models.Customer.company_id == current_user.company_id,
            models.Customer.is_active.is_(True),
        ).first()
        if not customer:
            raise HTTPException(status_code=400, detail="Customer account is invalid or inactive")

    sale = models.Sale(
        client_request_id=payload.client_request_id,
        branch_id=payload.branch_id,
        fuel_type_id=payload.fuel_type_id,
        customer_id=payload.customer_id,
        shift_id=shift.id,
        pump_id=pump.id if pump else None,
        volume=payload.volume,
        cost_price=price.current_cost,
        sell_price=price.sell_price,
        payment_method=payload.payment_method,
        served_by=current_user.id,
        entry_source="realtime",
        notes=payload.notes,
        sale_date=recorded_at,
    )
    db.add(sale)
    if pump is not None:
        # Keep the automatic pump totaliser in sync with accepted live sales.
        # Use an atomic SQL increment so concurrent cashier/attendant sales
        # cannot overwrite each other's meter updates.
        db.query(models.FuelPump).filter_by(id=pump.id).update(
            {
                models.FuelPump.meter_reading:
                    models.FuelPump.meter_reading + Decimal(str(payload.volume))
            },
            synchronize_session=False,
        )
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        # surfaces trigger errors cleanly, e.g. "Credit limit exceeded..." or "No stock..."
        raise HTTPException(status_code=400, detail=str(exc.orig))
    db.refresh(sale)
    return _sale_out(sale, _shift_stamps(db, [sale.shift_id]))


@router.get("", response_model=List[schemas.SaleOut])
def list_sales(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    branch_id: Optional[int] = None,
    fuel_type_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    served_by: Optional[int] = None,
    shift_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("attendant", "supervisor", "manager", "developer")),
):
    q = db.query(models.Sale).join(
        models.CompanyBranch, models.CompanyBranch.id == models.Sale.branch_id
    ).filter(models.CompanyBranch.company_id == current_user.company_id)

    if start_date:
        q = q.filter(models.Sale.sale_date >= start_date)
    if end_date:
        q = q.filter(models.Sale.sale_date <= end_date)
    if branch_id:
        q = q.filter(models.Sale.branch_id == branch_id)
    if fuel_type_id:
        q = q.filter(models.Sale.fuel_type_id == fuel_type_id)
    if customer_id:
        q = q.filter(models.Sale.customer_id == customer_id)
    if served_by:
        q = q.filter(models.Sale.served_by == served_by)
    if shift_id:
        q = q.filter(models.Sale.shift_id == shift_id)
    rows = q.order_by(models.Sale.sale_date.desc()).all()
    stamps = _shift_stamps(db, [s.shift_id for s in rows])
    return [_sale_out(s, stamps) for s in rows]


@router.get("/{sale_id}", response_model=schemas.SaleOut)
def get_sale(sale_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    sale = db.query(models.Sale).get(sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    auth.verify_branch_access(current_user, sale.branch_id, db)
    return _sale_out(sale, _shift_stamps(db, [sale.shift_id]))
