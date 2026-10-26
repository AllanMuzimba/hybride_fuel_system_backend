"""
Shift management.

A branch runs its forecourt in shifts. A shift carries the date + time period
('Morning' 06:00-14:00, ...), everyone who signs in is recorded in
shift_attendance, and every sale is booked against the shift it happened in.

Two recording modes, chosen when the shift is opened:
  * realtime     - attendants record each sale as it happens (Sales screen).
  * end_of_shift - nobody records individual sales; a supervisor/manager
                   selects the shift later and enters the litres sold, which
                   become real sales rows (stock, credit limits and reports
                   are updated by the same DB triggers as a live sale).

Sales are only accepted while the branch has an open shift, and only from
someone who has signed in for it.
"""
from datetime import date as Date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/shifts", tags=["shifts"])

# Close a shift, change its recording mode or enter end-of-shift volumes.
# 'Finance' has no schema role of its own - director/developer is the
# ownership/finance side, supervisor is the branch's shift supervisor.
SHIFT_MANAGE_ROLES = ("supervisor", "manager", "developer")
VOLUME_ENTRY_ROLES = ("supervisor",)


@router.post("/{shift_id}/settlement", response_model=schemas.ShiftOut)
def settle_shift_volumes(
    shift_id: int,
    payload: schemas.ShiftSettlementCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(*VOLUME_ENTRY_ROLES)),
):
    """Book each fuel's measured sold volume once. Customer purchases and
    electronic takings are allocated first; the remainder is cash. Sales
    triggers update stock, debt, and daily reports in one transaction."""
    shift = get_shift_or_404(db, shift_id, current_user)
    if shift.entry_mode != "end_of_shift":
        raise HTTPException(status_code=409, detail="Use live sales for this shift")
    if db.query(models.FuelPump.id).filter(
        models.FuelPump.branch_id == shift.branch_id,
        models.FuelPump.is_active.is_(True),
    ).first():
        raise HTTPException(status_code=409, detail="This branch sells through pumps; record end-of-shift litres per pump instead")
    if db.query(models.Sale.id).filter(models.Sale.shift_id == shift.id).first():
        raise HTTPException(status_code=409, detail="This shift already has sales; settlement can only be submitted once")
    totals = {row.fuel_type_id: row for row in payload.fuel_totals}
    if len(totals) != len(payload.fuel_totals):
        raise HTTPException(status_code=400, detail="Each fuel type must appear once")
    purchases: Dict[int, List[schemas.ShiftCustomerPurchase]] = {}
    for purchase in payload.customer_purchases:
        if purchase.fuel_type_id not in totals:
            raise HTTPException(status_code=400, detail="Customer purchase fuel is missing from totals")
        customer = db.query(models.Customer).filter(
            models.Customer.id == purchase.customer_id,
            models.Customer.company_id == current_user.company_id,
            models.Customer.is_active.is_(True),
        ).first()
        if not customer:
            raise HTTPException(status_code=400, detail="Customer account is invalid or inactive")
        purchases.setdefault(purchase.fuel_type_id, []).append(purchase)

    sale_date = datetime.combine(shift.shift_date, shift.start_time).astimezone()
    for fuel_id, row in totals.items():
        fuel = db.query(models.FuelType).filter(
            models.FuelType.id == fuel_id,
            models.FuelType.company_id == current_user.company_id,
        ).first()
        price = db.query(models.BranchFuelPrice).get((shift.branch_id, fuel_id))
        if not fuel or not price:
            raise HTTPException(status_code=400, detail=f"Fuel {fuel_id} has no branch price")
        if row.opening_volume is not None and row.closing_volume is not None:
            total = Decimal(str(row.opening_volume)) - Decimal(str(row.closing_volume))
        elif row.total_volume is not None:
            total = Decimal(str(row.total_volume))
        else:
            raise HTTPException(status_code=400, detail=f"Enter opening and closing volume for {fuel.name}")
        if total <= 0:
            raise HTTPException(status_code=400, detail=f"Closing volume must be below opening volume for {fuel.name}")
        card = Decimal(str(row.card_volume))
        mobile = Decimal(str(row.mobile_money_volume))
        account = sum((Decimal(str(p.volume)) for p in purchases.get(fuel_id, [])), Decimal(0))
        cash = total - card - mobile - account
        if cash < 0:
            raise HTTPException(status_code=400, detail=f"Allocated volume exceeds total for {fuel.name}")
        lines = [(cash, "cash", None), (card, "card", None), (mobile, "mobile_money", None)]
        lines += [(Decimal(str(p.volume)), "account", p.customer_id)
                  for p in purchases.get(fuel_id, [])]
        for volume, method, customer_id in lines:
            if volume == 0:
                continue
            db.add(models.Sale(
                branch_id=shift.branch_id, fuel_type_id=fuel_id,
                customer_id=customer_id, shift_id=shift.id, volume=volume,
                cost_price=price.current_cost, sell_price=price.sell_price,
                payment_method=method, served_by=current_user.id,
                sale_date=sale_date, entry_source="shift_close",
                notes="End of shift settlement",
            ))
        reading = db.query(models.ShiftFuelReading).filter(
            models.ShiftFuelReading.shift_id == shift.id,
            models.ShiftFuelReading.fuel_type_id == fuel_id,
        ).first()
        if reading:
            if row.opening_volume is not None:
                reading.opening_manual_volume = row.opening_volume
            reading.closing_system_volume = max(
                Decimal(0), reading.opening_system_volume - total)
            reading.closing_manual_volume = row.closing_volume
            reading.closing_dip_cm = row.closing_dip_cm
    if payload.cash_received is not None:
        db.flush()
        system_cash = (
            db.query(func.coalesce(func.sum(models.Sale.total_amount), 0))
            .filter(models.Sale.shift_id == shift.id, models.Sale.payment_method == "cash")
            .scalar()
        )
        shift.system_cash_amount = system_cash
        shift.counted_cash_amount = payload.cash_received
        shift.status = "closed"
        shift.closed_by = current_user.id
        shift.closed_at = datetime.now().astimezone()
        db.query(models.ShiftAttendance).filter(
            models.ShiftAttendance.shift_id == shift.id,
            models.ShiftAttendance.left_at.is_(None),
        ).update({models.ShiftAttendance.left_at: shift.closed_at}, synchronize_session=False)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc.orig))
    db.refresh(shift)
    return build_shift_out(db, shift, current_user.id)


@router.get("/templates", response_model=List[schemas.ShiftTemplateOut])
def list_templates(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    templates = (db.query(models.ShiftTemplate)
                 .filter(models.ShiftTemplate.company_id == current_user.company_id,
                         models.ShiftTemplate.is_active.is_(True))
                 .order_by(models.ShiftTemplate.start_time, models.ShiftTemplate.name).all())
    return [
        schemas.ShiftTemplateOut(
            id=row.id,
            company_id=row.company_id,
            name=row.name,
            start_time=row.start_time,
            end_time=row.end_time,
            is_active=row.is_active,
            usage_count=db.query(func.count(models.Shift.id)).filter(
                models.Shift.company_id == current_user.company_id,
                models.Shift.shift_label == row.name,
            ).scalar() or 0,
        )
        for row in templates
    ]


@router.post("/templates", response_model=schemas.ShiftTemplateOut, status_code=201)
def create_template(
    payload: schemas.ShiftTemplateCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "developer")),
):
    if payload.start_time == payload.end_time:
        raise HTTPException(status_code=400, detail="Start and end time must differ")
    if payload.name.strip().lower() == "custom":
        raise HTTPException(status_code=400, detail="Custom is reserved for an unsaved shift period")
    template = models.ShiftTemplate(
        company_id=current_user.company_id, name=payload.name.strip(),
        start_time=payload.start_time, end_time=payload.end_time,
    )
    db.add(template)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Shift name already exists for this company")
    db.refresh(template)
    return template


@router.put("/templates/{template_id}", response_model=schemas.ShiftTemplateOut)
def update_template(
    template_id: int,
    payload: schemas.ShiftTemplateCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "developer")),
):
    template = db.query(models.ShiftTemplate).filter(
        models.ShiftTemplate.id == template_id,
        models.ShiftTemplate.company_id == current_user.company_id,
        models.ShiftTemplate.is_active.is_(True),
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="Shift template not found")
    if payload.start_time == payload.end_time:
        raise HTTPException(status_code=400, detail="Start and end time must differ")
    if payload.name.strip().lower() == "custom":
        raise HTTPException(status_code=400, detail="Custom is reserved for an unsaved shift period")
    template.name = payload.name.strip()
    template.start_time = payload.start_time
    template.end_time = payload.end_time
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Shift name already exists for this company")
    db.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "developer")),
):
    template = db.query(models.ShiftTemplate).filter(
        models.ShiftTemplate.id == template_id,
        models.ShiftTemplate.company_id == current_user.company_id,
        models.ShiftTemplate.is_active.is_(True),
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="Shift template not found")
    used = db.query(models.Shift.id).filter(
        models.Shift.company_id == current_user.company_id,
        models.Shift.shift_label == template.name,
    ).first()
    if used:
        raise HTTPException(
            status_code=409,
            detail="This shift has activity and cannot be removed because it is required by finance reports",
        )
    template.is_active = False
    db.commit()


# ------------------------------------------------------------- helpers ----
def get_shift_or_404(db: Session, shift_id: int, user: models.User) -> models.Shift:
    shift = db.query(models.Shift).get(shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    auth.verify_branch_access(user, shift.branch_id, db)
    return shift


def require_realtime_shift(db: Session, branch_id: int) -> models.Shift:
    """The branch must have an open shift that records sales live. Called by
    POST /sales so nothing can be sold outside a shift period."""
    shift = (
        db.query(models.Shift)
        .filter(models.Shift.branch_id == branch_id, models.Shift.status == "open")
        .first()
    )
    if not shift:
        raise HTTPException(
            status_code=409,
            detail="No open shift at this branch - open a shift before recording sales",
        )
    if shift.entry_mode != "realtime":
        raise HTTPException(
            status_code=400,
            detail="This shift records the volume sold at the end of the shift - "
                   "enter it on the Shifts page instead",
        )
    return shift


def require_on_shift(db: Session, shift: models.Shift, user: models.User) -> None:
    joined = (
        db.query(models.ShiftAttendance)
        .filter(models.ShiftAttendance.shift_id == shift.id,
                models.ShiftAttendance.user_id == user.id)
        .first()
    )
    if not joined:
        raise HTTPException(
            status_code=409,
            detail="You have not signed in for this shift - start your shift first",
        )
    if joined.left_at is not None:
        raise HTTPException(status_code=409, detail="You left this shift; join it again before recording sales")


def _attendance_by_shift(db: Session, shift_ids: List[int]) -> Dict[int, List[schemas.ShiftAttendeeOut]]:
    if not shift_ids:
        return {}
    rows = (
        db.query(models.ShiftAttendance, models.User)
        .join(models.User, models.User.id == models.ShiftAttendance.user_id)
        .filter(models.ShiftAttendance.shift_id.in_(shift_ids))
        .order_by(models.ShiftAttendance.joined_at, models.ShiftAttendance.id)
        .all()
    )
    out: Dict[int, List[schemas.ShiftAttendeeOut]] = {}
    for att, user in rows:
        out.setdefault(att.shift_id, []).append(
            schemas.ShiftAttendeeOut(
                user_id=user.id,
                username=user.username,
                full_name=user.full_name,
                role=user.role,
                joined_at=att.joined_at,
                left_at=att.left_at,
                system_cash_amount=(float(att.system_cash_amount)
                                    if att.system_cash_amount is not None else None),
                counted_cash_amount=(float(att.counted_cash_amount)
                                     if att.counted_cash_amount is not None else None),
                cash_variance=(float(att.counted_cash_amount - att.system_cash_amount)
                               if att.counted_cash_amount is not None and att.system_cash_amount is not None else None),
            )
        )
    return out


def _totals_by_shift(db: Session, shift_ids: List[int]) -> Dict[int, dict]:
    if not shift_ids:
        return {}
    rows = (
        db.query(
            models.Sale.shift_id,
            models.Sale.entry_source,
            models.Sale.payment_method,
            func.count(models.Sale.id),
            func.coalesce(func.sum(models.Sale.volume), 0),
            func.coalesce(func.sum(models.Sale.total_amount), 0),
        )
        .filter(models.Sale.shift_id.in_(shift_ids))
        .group_by(models.Sale.shift_id, models.Sale.entry_source, models.Sale.payment_method)
        .all()
    )
    out: Dict[int, dict] = {}
    for shift_id, source, method, count, volume, amount in rows:
        bucket = out.setdefault(shift_id, {
            "sale_count": 0, "realtime_volume": 0.0, "realtime_amount": 0.0,
            "entry_volume": 0.0, "entry_amount": 0.0,
            "cash_amount": 0.0, "card_amount": 0.0,
            "mobile_money_amount": 0.0, "account_amount": 0.0,
        })
        bucket["sale_count"] += int(count)
        key = "realtime" if source == "realtime" else "entry"
        bucket[f"{key}_volume"] += float(volume)
        bucket[f"{key}_amount"] += float(amount)
        bucket[f"{method}_amount"] += float(amount)
    return out


def build_shift_out(
    db: Session,
    shift: models.Shift,
    current_user_id: int,
    attendance: Optional[Dict[int, List[schemas.ShiftAttendeeOut]]] = None,
    totals: Optional[Dict[int, dict]] = None,
    branch_name: Optional[str] = None,
) -> schemas.ShiftOut:
    if attendance is None:
        attendance = _attendance_by_shift(db, [shift.id])
    if totals is None:
        totals = _totals_by_shift(db, [shift.id])
    if branch_name is None:
        branch = db.query(models.CompanyBranch).get(shift.branch_id)
        branch_name = branch.branch_name if branch else ""
    opener = db.query(models.User).get(shift.opened_by)
    people = attendance.get(shift.id, [])
    t = totals.get(shift.id, {})
    reading_rows = (
        db.query(models.ShiftFuelReading, models.FuelType.name)
        .join(models.FuelType, models.FuelType.id == models.ShiftFuelReading.fuel_type_id)
        .filter(models.ShiftFuelReading.shift_id == shift.id)
        .order_by(models.FuelType.name)
        .all()
    )
    fuel_readings = []
    for reading, fuel_name in reading_rows:
        opening_manual = (float(reading.opening_manual_volume)
                          if reading.opening_manual_volume is not None else None)
        closing_manual = (float(reading.closing_manual_volume)
                          if reading.closing_manual_volume is not None else None)
        opening_system = float(reading.opening_system_volume)
        closing_system = (float(reading.closing_system_volume)
                          if reading.closing_system_volume is not None else None)
        fuel_readings.append(schemas.ShiftFuelReadingOut(
            fuel_type_id=reading.fuel_type_id,
            fuel_name=fuel_name,
            opening_system_volume=opening_system,
            opening_manual_volume=opening_manual,
            opening_dip_cm=(float(reading.opening_dip_cm)
                            if reading.opening_dip_cm is not None else None),
            opening_variance=(opening_manual - opening_system
                              if opening_manual is not None else None),
            closing_system_volume=closing_system,
            closing_manual_volume=closing_manual,
            closing_dip_cm=(float(reading.closing_dip_cm)
                            if reading.closing_dip_cm is not None else None),
            closing_variance=(closing_manual - closing_system
                              if closing_manual is not None and closing_system is not None else None),
        ))
    return schemas.ShiftOut(
        id=shift.id,
        company_id=shift.company_id,
        branch_id=shift.branch_id,
        branch_name=branch_name,
        shift_date=shift.shift_date,
        shift_label=shift.shift_label,
        start_time=shift.start_time,
        end_time=shift.end_time,
        entry_mode=shift.entry_mode,
        status=shift.status,
        opened_by=shift.opened_by,
        opened_by_name=opener.full_name if opener else "",
        opened_at=shift.opened_at,
        closed_by=shift.closed_by,
        closed_at=shift.closed_at,
        notes=shift.notes,
        attendees=people,
        sale_count=t.get("sale_count", 0),
        realtime_volume=t.get("realtime_volume", 0.0),
        realtime_amount=t.get("realtime_amount", 0.0),
        entry_volume=t.get("entry_volume", 0.0),
        entry_amount=t.get("entry_amount", 0.0),
        total_volume=t.get("realtime_volume", 0.0) + t.get("entry_volume", 0.0),
        total_amount=t.get("realtime_amount", 0.0) + t.get("entry_amount", 0.0),
        cash_amount=t.get("cash_amount", 0.0),
        card_amount=t.get("card_amount", 0.0),
        mobile_money_amount=t.get("mobile_money_amount", 0.0),
        account_amount=t.get("account_amount", 0.0),
        current_user_joined=any(p.user_id == current_user_id and p.left_at is None for p in people),
        fuel_readings=fuel_readings,
        system_cash_amount=(float(shift.system_cash_amount)
                            if shift.system_cash_amount is not None else None),
        counted_cash_amount=(float(shift.counted_cash_amount)
                             if shift.counted_cash_amount is not None else None),
        cash_variance=(float(shift.counted_cash_amount - shift.system_cash_amount)
                       if shift.counted_cash_amount is not None and shift.system_cash_amount is not None else None),
    )


def _can_manage(user: models.User, shift: models.Shift) -> bool:
    return user.id == shift.opened_by or user.role in SHIFT_MANAGE_ROLES


# ------------------------------------------------------------- queries ----
@router.get("", response_model=List[schemas.ShiftOut])
def list_shifts(
    branch_id: Optional[int] = None,
    status: Optional[str] = None,
    shift_date: Optional[Date] = None,
    start: Optional[Date] = None,
    end: Optional[Date] = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Shifts for your company (a branch-tied user only sees their branch),
    newest first, with attendance and the sales booked to each shift."""
    q = (
        db.query(models.Shift, models.CompanyBranch.branch_name)
        .join(models.CompanyBranch, models.CompanyBranch.id == models.Shift.branch_id)
        .filter(models.CompanyBranch.company_id == current_user.company_id)
    )
    wanted_branch = branch_id or current_user.branch_id
    if wanted_branch:
        q = q.filter(models.Shift.branch_id == wanted_branch)
    if status:
        q = q.filter(models.Shift.status == status)
    if shift_date:
        q = q.filter(models.Shift.shift_date == shift_date)
    if start:
        q = q.filter(models.Shift.shift_date >= start)
    if end:
        q = q.filter(models.Shift.shift_date <= end)

    rows = (
        q.order_by(models.Shift.shift_date.desc(), models.Shift.start_time.desc())
        .limit(limit)
        .all()
    )
    ids = [s.id for s, _ in rows]
    attendance = _attendance_by_shift(db, ids)
    totals = _totals_by_shift(db, ids)
    return [
        build_shift_out(db, shift, current_user.id, attendance, totals, branch_name)
        for shift, branch_name in rows
    ]


@router.get("/active", response_model=schemas.ShiftOut)
def active_shift(
    branch_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """The branch's open shift right now (404 when none is open)."""
    wanted = branch_id or current_user.branch_id
    if wanted is None:
        raise HTTPException(
            status_code=400,
            detail="branch_id is required (your account isn't tied to a single branch)",
        )
    auth.verify_branch_access(current_user, wanted, db)
    shift = (
        db.query(models.Shift)
        .filter(models.Shift.branch_id == wanted, models.Shift.status == "open")
        .first()
    )
    if not shift:
        raise HTTPException(status_code=404, detail="No open shift at this branch")
    return build_shift_out(db, shift, current_user.id)


@router.get("/{shift_id}", response_model=schemas.ShiftOut)
def get_shift(
    shift_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    shift = get_shift_or_404(db, shift_id, current_user)
    return build_shift_out(db, shift, current_user.id)


# ------------------------------------------------------------- actions ----
@router.post("", response_model=schemas.ShiftOut, status_code=201)
def open_shift(
    payload: schemas.ShiftCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Open a shift at a branch and sign the opener in for it. Only one shift
    may be open per branch (enforced here and by a partial unique index)."""
    auth.verify_branch_access(current_user, payload.branch_id, db)
    if payload.start_time == payload.end_time:
        raise HTTPException(status_code=400, detail="Start and end time must differ")

    existing = (
        db.query(models.Shift)
        .filter(models.Shift.branch_id == payload.branch_id, models.Shift.status == "open")
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A shift is already open at this branch "
                   f"({existing.shift_label} {existing.start_time:%H:%M}-{existing.end_time:%H:%M})",
        )

    shift = models.Shift(
        company_id=current_user.company_id,
        branch_id=payload.branch_id,
        shift_date=payload.shift_date or Date.today(),
        shift_label=payload.shift_label or "Custom",
        start_time=payload.start_time,
        end_time=payload.end_time,
        entry_mode=payload.entry_mode,
        status="open",
        opened_by=current_user.id,
        notes=payload.notes,
    )
    db.add(shift)
    db.flush()
    supplied = {row.fuel_type_id: row for row in payload.opening_readings}
    if len(supplied) != len(payload.opening_readings):
        raise HTTPException(status_code=400, detail="Each opening fuel type must appear once")
    stock_rows = db.query(models.FuelStock).filter(
        models.FuelStock.branch_id == payload.branch_id).all()
    for stock in stock_rows:
        entered = supplied.get(stock.fuel_type_id)
        previous = (
            db.query(models.ShiftFuelReading.closing_manual_volume)
            .join(models.Shift, models.Shift.id == models.ShiftFuelReading.shift_id)
            .filter(
                models.Shift.branch_id == payload.branch_id,
                models.ShiftFuelReading.fuel_type_id == stock.fuel_type_id,
                models.ShiftFuelReading.closing_manual_volume.isnot(None),
            )
            .order_by(models.Shift.closed_at.desc(), models.Shift.id.desc())
            .first()
        )
        automatic = previous[0] if previous else stock.current_volume
        db.add(models.ShiftFuelReading(
            shift_id=shift.id,
            fuel_type_id=stock.fuel_type_id,
            opening_system_volume=automatic,
            opening_manual_volume=entered.manual_volume if entered else None,
            opening_dip_cm=entered.dip_cm if entered else None,
        ))
    db.add(models.ShiftAttendance(shift_id=shift.id, user_id=current_user.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A shift is already open at this branch")
    db.refresh(shift)
    return build_shift_out(db, shift, current_user.id)


@router.post("/{shift_id}/join", response_model=schemas.ShiftOut)
def join_shift(
    shift_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Sign in to a shift - this is the attendance record the Shifts page
    shows (who was on shift, and when they joined)."""
    shift = get_shift_or_404(db, shift_id, current_user)
    if shift.status != "open":
        raise HTTPException(status_code=400, detail="That shift is already closed")

    row = (
        db.query(models.ShiftAttendance)
        .filter(models.ShiftAttendance.shift_id == shift.id,
                models.ShiftAttendance.user_id == current_user.id)
        .first()
    )
    if row is None:
        db.add(models.ShiftAttendance(shift_id=shift.id, user_id=current_user.id))
        db.commit()
    elif row.left_at is not None:
        row.left_at = None
        db.commit()
        db.refresh(shift)
    return build_shift_out(db, shift, current_user.id)


@router.post("/{shift_id}/leave", response_model=schemas.ShiftOut)
def leave_shift(
    shift_id: int,
    payload: schemas.ShiftLeaveCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    shift = get_shift_or_404(db, shift_id, current_user)
    row = (
        db.query(models.ShiftAttendance)
        .filter(models.ShiftAttendance.shift_id == shift.id,
                models.ShiftAttendance.user_id == current_user.id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=400, detail="You are not signed in for this shift")
    if row.left_at is None:
        if shift.entry_mode == "realtime":
            if payload.cash_received is None:
                raise HTTPException(status_code=400, detail="Enter counted cash before leaving this shift")
            system_cash = (
                db.query(func.coalesce(func.sum(models.Sale.total_amount), 0))
                .filter(
                    models.Sale.shift_id == shift.id,
                    models.Sale.served_by == current_user.id,
                    models.Sale.payment_method == "cash",
                )
                .scalar()
            )
            row.system_cash_amount = system_cash
            row.counted_cash_amount = payload.cash_received
        row.left_at = datetime.now().astimezone()
        db.commit()
    return build_shift_out(db, shift, current_user.id)


@router.post("/{shift_id}/close", response_model=schemas.ShiftOut)
def close_shift(
    shift_id: int,
    payload: schemas.ShiftCloseCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Close a shift. Only a supervisor may enter closing readings and close it.
    Everyone still signed in is marked as having left at close time."""
    shift = get_shift_or_404(db, shift_id, current_user)
    if current_user.role != "supervisor":
        raise HTTPException(status_code=403, detail="Only a supervisor can close a shift")
    if shift.status != "open":
        raise HTTPException(status_code=400, detail="That shift is already closed")
    readings = db.query(models.ShiftFuelReading).filter(
        models.ShiftFuelReading.shift_id == shift.id).all()
    supplied = {row.fuel_type_id: row for row in payload.closing_readings}
    if len(supplied) != len(payload.closing_readings):
        raise HTTPException(status_code=400, detail="Each closing fuel type must appear once")
    expected_ids = {row.fuel_type_id for row in readings}
    if set(supplied) != expected_ids:
        raise HTTPException(
            status_code=400,
            detail="Enter closing volume and dip readings for every fuel in this shift",
        )
    stock = {
        row.fuel_type_id: row.current_volume
        for row in db.query(models.FuelStock).filter(
            models.FuelStock.branch_id == shift.branch_id).all()
    }
    for reading in readings:
        entered = supplied[reading.fuel_type_id]
        if entered.manual_volume is None:
            raise HTTPException(
                status_code=400,
                detail="Closing volume is required for every fuel",
            )
        reading.closing_system_volume = stock.get(reading.fuel_type_id, 0)
        reading.closing_manual_volume = entered.manual_volume
        reading.closing_dip_cm = entered.dip_cm

    system_cash = (
        db.query(func.coalesce(func.sum(models.Sale.total_amount), 0))
        .filter(models.Sale.shift_id == shift.id, models.Sale.payment_method == "cash")
        .scalar()
    )
    shift.system_cash_amount = system_cash
    shift.counted_cash_amount = payload.cash_received

    shift.status = "closed"
    shift.closed_by = current_user.id
    shift.closed_at = datetime.now().astimezone()
    db.query(models.ShiftAttendance).filter(
        models.ShiftAttendance.shift_id == shift.id,
        models.ShiftAttendance.left_at.is_(None),
    ).update({models.ShiftAttendance.left_at: shift.closed_at},
             synchronize_session=False)
    db.commit()
    db.refresh(shift)
    return build_shift_out(db, shift, current_user.id)


@router.patch("/{shift_id}/mode", response_model=schemas.ShiftOut)
def set_entry_mode(
    shift_id: int,
    payload: schemas.ShiftModeUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Switch how a shift records sales (live vs end-of-shift totals). Only
    allowed while the shift has no sales at all, so nothing can be counted
    twice or silently moved between the two methods."""
    shift = get_shift_or_404(db, shift_id, current_user)
    if not _can_manage(current_user, shift):
        raise HTTPException(status_code=403, detail="Not allowed to change this shift's mode")
    sold = db.query(func.count(models.Sale.id)).filter(models.Sale.shift_id == shift.id).scalar() or 0
    if sold:
        raise HTTPException(
            status_code=400,
            detail="This shift already has sales recorded - the recording mode can no longer be changed",
        )
    shift.entry_mode = payload.entry_mode
    db.commit()
    db.refresh(shift)
    return build_shift_out(db, shift, current_user.id)


@router.post("/{shift_id}/volume-entries", response_model=schemas.ShiftOut)
def enter_shift_volumes(
    shift_id: int,
    payload: schemas.ShiftVolumeEntryCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """End-of-shift entry: a supervisor/manager picks a shift and types the
    litres sold per fuel type. Each entry becomes a real sale row dated back
    to the shift's own period, so tank stock, credit limits, the daily reports
    and the audit log all update exactly as they would for a live sale."""
    if current_user.role not in VOLUME_ENTRY_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{current_user.role}' is not permitted to enter end-of-shift volumes",
        )
    shift = get_shift_or_404(db, shift_id, current_user)

    if shift.entry_mode != "end_of_shift":
        raise HTTPException(
            status_code=400,
            detail="This shift records sales live - switch it to end-of-shift mode first",
        )
    realtime = (
        db.query(func.count(models.Sale.id))
        .filter(models.Sale.shift_id == shift.id, models.Sale.entry_source == "realtime")
        .scalar()
        or 0
    )
    if realtime:
        raise HTTPException(
            status_code=400,
            detail="This shift already has live sales recorded - volumes can only be "
                   "entered on a shift run in end-of-shift mode",
        )

    # Sales are dated into the shift's own period so the daily reports bucket
    # them on the shift's day, not on the day somebody happened to type them.
    entry_moment = datetime.combine(shift.shift_date, shift.start_time).astimezone()

    created = 0
    for entry in payload.entries:
        fuel = db.query(models.FuelType).get(entry.fuel_type_id)
        if not fuel or fuel.company_id != current_user.company_id:
            raise HTTPException(status_code=404, detail="Fuel type not found in your company")
        price = db.query(models.BranchFuelPrice).get((shift.branch_id, entry.fuel_type_id))
        if not price:
            raise HTTPException(
                status_code=400,
                detail=f"This branch has no price set for '{fuel.name}' yet",
            )
        if entry.payment_method == "account" and not entry.customer_id:
            raise HTTPException(
                status_code=400,
                detail=f"customer_id is required for account sales ({fuel.name})",
            )
        db.add(models.Sale(
            branch_id=shift.branch_id,
            fuel_type_id=entry.fuel_type_id,
            customer_id=entry.customer_id,
            shift_id=shift.id,
            volume=entry.volume,
            cost_price=price.current_cost,
            sell_price=price.sell_price,
            payment_method=entry.payment_method,
            served_by=current_user.id,
            sale_date=entry_moment,
            entry_source="shift_close",
            notes=entry.notes or payload.notes,
        ))
        created += 1

    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc.orig))
    db.refresh(shift)
    return build_shift_out(db, shift, current_user.id)
