import secrets
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .. import auth, models
from ..database import get_db
from .shifts_router import (
    VOLUME_ENTRY_ROLES,
    get_shift_or_404,
    require_on_shift,
    require_realtime_shift,
)

router = APIRouter(prefix="/pumps", tags=["pumps & fuel vouchers"])


class PumpWrite(BaseModel):
    branch_id: int
    tank_id: int
    name: str = Field(min_length=1, max_length=100)
    meter_reading: float = Field(default=0, ge=0)


class PumpAssignmentWrite(BaseModel):
    shift_id: int
    pump_id: int
    user_id: int
    opening_meter: Optional[float] = Field(default=None, ge=0)


class VoucherCreate(BaseModel):
    branch_id: int
    fuel_type_id: int
    amount: float = Field(gt=0)
    payment_method: str = "cash"
    customer_id: Optional[int] = None


class VoucherRedeem(BaseModel):
    pump_id: int


class PumpCloseWrite(BaseModel):
    closing_meter: float = Field(ge=0)


class PumpSettlementEntry(BaseModel):
    pump_id: int
    volume: Optional[float] = Field(default=None, gt=0)
    manual_closing_meter: Optional[float] = Field(default=None, ge=0)
    card_volume: float = Field(default=0, ge=0)
    mobile_money_volume: float = Field(default=0, ge=0)


class PumpSettlementWrite(BaseModel):
    shift_id: int
    entries: List[PumpSettlementEntry] = Field(min_length=1)
    cash_received: Optional[float] = Field(default=None, ge=0)


def _pump_out(db: Session, pump: models.FuelPump) -> dict:
    tank = db.query(models.FuelTank).get(pump.tank_id)
    return {
        "id": pump.id, "branch_id": pump.branch_id, "tank_id": pump.tank_id,
        "tank_name": tank.name, "fuel_type_id": tank.fuel_type_id,
        "name": pump.name, "meter_reading": float(pump.meter_reading),
        "is_active": pump.is_active,
    }


@router.get("")
def list_pumps(branch_id: Optional[int] = None, tank_id: Optional[int] = None,
               db: Session = Depends(get_db),
               current_user: models.User = Depends(auth.get_current_user)):
    q = db.query(models.FuelPump).join(models.CompanyBranch).filter(
        models.CompanyBranch.company_id == current_user.company_id)
    if current_user.branch_id is not None:
        q = q.filter(models.FuelPump.branch_id == current_user.branch_id)
    elif branch_id is not None:
        q = q.filter(models.FuelPump.branch_id == branch_id)
    if tank_id is not None:
        q = q.filter(models.FuelPump.tank_id == tank_id)
    return [_pump_out(db, p) for p in q.order_by(models.FuelPump.name).all()]


@router.post("")
def create_pump(payload: PumpWrite, db: Session = Depends(get_db),
                current_user: models.User = Depends(
                    auth.require_role("manager", "director", "developer", "dev_support"))):
    auth.verify_branch_access(current_user, payload.branch_id, db)
    tank = db.query(models.FuelTank).get(payload.tank_id)
    if not tank or tank.branch_id != payload.branch_id:
        raise HTTPException(status_code=404, detail="Tank not found at that branch")
    pump = models.FuelPump(branch_id=payload.branch_id, tank_id=tank.id,
                           name=payload.name.strip(), meter_reading=payload.meter_reading)
    db.add(pump)
    db.commit(); db.refresh(pump)
    return _pump_out(db, pump)


@router.post("/assignments")
def assign_pump(payload: PumpAssignmentWrite, db: Session = Depends(get_db),
                current_user: models.User = Depends(
                    auth.require_role("supervisor", "manager", "director", "developer", "dev_support"))):
    shift = db.query(models.Shift).get(payload.shift_id)
    pump = db.query(models.FuelPump).get(payload.pump_id)
    user = db.query(models.User).get(payload.user_id)
    if not shift or not pump or not user or pump.branch_id != shift.branch_id:
        raise HTTPException(status_code=404, detail="Shift, pump, or attendant is invalid")
    auth.verify_branch_access(current_user, shift.branch_id, db)
    membership = db.query(models.UserCompanyMembership).filter(
        models.UserCompanyMembership.user_id == user.id,
        models.UserCompanyMembership.company_id == current_user.company_id,
        models.UserCompanyMembership.is_active.is_(True),
    ).first()
    if membership is None or user.role != "attendant":
        raise HTTPException(status_code=400, detail="Pump can only be assigned to an attendant in this company")
    if membership.branch_id is not None and membership.branch_id != shift.branch_id:
        raise HTTPException(status_code=403, detail="Attendant is not assigned to this branch")
    if not db.query(models.ShiftAttendance).filter_by(shift_id=shift.id, user_id=user.id).first():
        raise HTTPException(status_code=409, detail="Attendant must join the shift before pump assignment")
    assignment = models.ShiftPumpAssignment(
        shift_id=shift.id, pump_id=pump.id, user_id=user.id,
        opening_meter=payload.opening_meter if payload.opening_meter is not None else pump.meter_reading)
    db.add(assignment); db.commit(); db.refresh(assignment)
    return {"id": assignment.id, "shift_id": shift.id, "pump_id": pump.id,
            "pump_name": pump.name, "user_id": user.id,
            "opening_meter": float(assignment.opening_meter)}


@router.get("/assigned")
def assigned_pumps(branch_id: Optional[int] = None, db: Session = Depends(get_db),
                   current_user: models.User = Depends(auth.get_current_user)):
    wanted_branch = branch_id or current_user.branch_id
    if wanted_branch is None:
        return []
    auth.verify_branch_access(current_user, wanted_branch, db)
    shift = require_realtime_shift(db, wanted_branch)
    require_on_shift(db, shift, current_user)
    rows = db.query(models.ShiftPumpAssignment, models.FuelPump).join(
        models.FuelPump, models.FuelPump.id == models.ShiftPumpAssignment.pump_id
    ).filter(models.ShiftPumpAssignment.shift_id == shift.id,
             models.ShiftPumpAssignment.user_id == current_user.id,
             models.FuelPump.is_active.is_(True)).all()
    return [{**_pump_out(db, p), "shift_id": shift.id,
             "opening_meter": float(a.opening_meter)} for a, p in rows]


@router.get("/assignments/{shift_id}")
def shift_assignments(shift_id: int, db: Session = Depends(get_db),
                      current_user: models.User = Depends(auth.get_current_user)):
    shift = db.query(models.Shift).get(shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    auth.verify_branch_access(current_user, shift.branch_id, db)
    rows = db.query(models.ShiftPumpAssignment, models.FuelPump, models.User).join(
        models.FuelPump, models.FuelPump.id == models.ShiftPumpAssignment.pump_id
    ).join(models.User, models.User.id == models.ShiftPumpAssignment.user_id).filter(
        models.ShiftPumpAssignment.shift_id == shift_id).all()
    return [{"id": a.id, "pump_id": p.id, "pump_name": p.name,
             "user_id": u.id, "attendant_name": u.full_name,
             "opening_meter": float(a.opening_meter),
             "closing_meter": float(a.closing_meter) if a.closing_meter is not None else None,
             "meter_volume": (float(a.closing_meter - a.opening_meter)
                              if a.closing_meter is not None else None)} for a, p, u in rows]


@router.patch("/assignments/{assignment_id}/close")
def close_assignment(assignment_id: int, payload: PumpCloseWrite,
                     db: Session = Depends(get_db),
                     current_user: models.User = Depends(
                         auth.require_role("supervisor", "manager", "director", "developer"))):
    assignment = db.query(models.ShiftPumpAssignment).get(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Pump assignment not found")
    shift = db.query(models.Shift).get(assignment.shift_id)
    auth.verify_branch_access(current_user, shift.branch_id, db)
    if payload.closing_meter < float(assignment.opening_meter):
        raise HTTPException(status_code=400, detail="Closing meter cannot be below opening meter")
    assignment.closing_meter = payload.closing_meter
    db.commit()
    return {"id": assignment.id, "opening_meter": float(assignment.opening_meter),
            "closing_meter": float(assignment.closing_meter),
            "meter_volume": float(assignment.closing_meter - assignment.opening_meter)}


@router.post("/settlement")
def settle_pump_volumes(payload: PumpSettlementWrite, db: Session = Depends(get_db),
                        current_user: models.User = Depends(auth.require_role(*VOLUME_ENTRY_ROLES))):
    """Zimbabwe-style end of shift: attendants never touch the system, so the
    supervisor reads each pump board and types the litres sold per pump. Every
    line becomes a pump-linked sale — the sale trigger deducts exactly that
    pump's tank and advances its meter, so the next shift starts with the
    correct balance. Closing the shift is optional and follows the same cash
    variance rules as the fuel-type settlement."""
    shift = get_shift_or_404(db, payload.shift_id, current_user)
    if shift.entry_mode != "end_of_shift":
        raise HTTPException(status_code=409, detail="Use live sales for this shift")
    if db.query(models.Sale.id).filter(models.Sale.shift_id == shift.id).first():
        raise HTTPException(status_code=409, detail="This shift already has sales; settlement can only be submitted once")
    seen = set()
    for entry in payload.entries:
        if entry.pump_id in seen:
            raise HTTPException(status_code=400, detail="Each pump can appear only once")
        seen.add(entry.pump_id)

    sale_date = datetime.combine(shift.shift_date, shift.start_time).astimezone()
    summary: List[dict] = []
    closing_by_pump: Dict[int, Decimal] = {}
    fuel_totals: Dict[int, Decimal] = {}
    for entry in payload.entries:
        pump = db.query(models.FuelPump).get(entry.pump_id)
        tank = db.query(models.FuelTank).get(pump.tank_id) if pump else None
        if not pump or not tank or pump.branch_id != shift.branch_id or not pump.is_active:
            raise HTTPException(status_code=400, detail="One of the pumps is not active at this branch")
        price = db.query(models.BranchFuelPrice).get((shift.branch_id, tank.fuel_type_id))
        if not price:
            raise HTTPException(status_code=400, detail="Fuel price is not configured for a pump's fuel")
        assignment = db.query(models.ShiftPumpAssignment).filter_by(
            shift_id=shift.id, pump_id=pump.id).first()
        opening = Decimal(str(assignment.opening_meter if assignment else pump.meter_reading))
        entered_volume = Decimal(str(entry.volume)) if entry.volume is not None else None
        manual_closing = (
            Decimal(str(entry.manual_closing_meter))
            if entry.manual_closing_meter is not None else None
        )
        if entered_volume is None and manual_closing is None:
            raise HTTPException(
                status_code=400,
                detail=f"Enter litres sold or a manual closing meter for {pump.name}",
            )
        if manual_closing is not None and manual_closing < opening:
            raise HTTPException(
                status_code=400,
                detail=f"Manual closing meter for {pump.name} cannot be below its opening meter",
            )
        meter_volume = manual_closing - opening if manual_closing is not None else None
        # When a manual totaliser is supplied, its delta is the actual volume
        # booked. The separately entered litres remain as a reconciliation
        # figure and are reported as meter variance.
        volume = meter_volume if meter_volume is not None else entered_volume
        assert volume is not None
        card = Decimal(str(entry.card_volume))
        mobile = Decimal(str(entry.mobile_money_volume))
        if card + mobile > volume:
            raise HTTPException(status_code=400, detail="Card and mobile litres exceed a pump's measured total")
        for line_volume, method in ((volume - card - mobile, "cash"), (card, "card"), (mobile, "mobile_money")):
            if line_volume == 0:
                continue
            db.add(models.Sale(
                branch_id=shift.branch_id, fuel_type_id=tank.fuel_type_id,
                shift_id=shift.id, pump_id=pump.id, volume=line_volume,
                cost_price=price.current_cost, sell_price=price.sell_price,
                payment_method=method,
                served_by=assignment.user_id if assignment else current_user.id,
                sale_date=sale_date, entry_source="shift_close",
                notes=f"End of shift pump readings · {pump.name}",
            ))
        closing = manual_closing if manual_closing is not None else opening + volume
        closing_by_pump[pump.id] = closing
        if assignment:
            assignment.closing_meter = closing
        fuel_totals[tank.fuel_type_id] = fuel_totals.get(tank.fuel_type_id, Decimal(0)) + volume
        summary.append({"pump_id": pump.id, "pump_name": pump.name,
                        "tank_id": tank.id, "tank_name": tank.name,
                        "fuel_type_id": tank.fuel_type_id,
                        "opening_meter": float(opening), "volume": float(volume),
                        "system_closing_meter": float(opening + volume),
                        "manual_closing_meter": float(manual_closing) if manual_closing is not None else None,
                        "meter_volume": float(meter_volume) if meter_volume is not None else None,
                        "entered_volume": float(entered_volume) if entered_volume is not None else None,
                        "variance_litres": (
                            float(meter_volume - entered_volume)
                            if meter_volume is not None and entered_volume is not None else None
                        ),
                        "closing_meter": float(closing)})
    db.flush()
    for pump_id, closing in closing_by_pump.items():
        db.query(models.FuelPump).filter_by(id=pump_id).update(
            {models.FuelPump.meter_reading: closing},
            synchronize_session=False,
        )
    for fuel_id, total in fuel_totals.items():
        reading = db.query(models.ShiftFuelReading).filter_by(
            shift_id=shift.id, fuel_type_id=fuel_id).first()
        if reading:
            reading.closing_system_volume = max(
                Decimal(0), reading.opening_system_volume - total)
            reading.closing_manual_volume = reading.closing_system_volume
    if payload.cash_received is not None:
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
    return {"shift_id": shift.id, "status": shift.status,
            "volume": float(sum(fuel_totals.values(), Decimal(0))), "pumps": summary}


@router.post("/vouchers")
def create_voucher(payload: VoucherCreate, db: Session = Depends(get_db),
                   current_user: models.User = Depends(
                       auth.require_role("attendant", "cashier", "secretary", "supervisor", "manager", "director", "developer"))):
    auth.verify_branch_access(current_user, payload.branch_id, db)
    shift = require_realtime_shift(db, payload.branch_id)
    price = db.query(models.BranchFuelPrice).get((payload.branch_id, payload.fuel_type_id))
    if not price or price.sell_price <= 0:
        raise HTTPException(status_code=400, detail="Fuel price is not configured")
    volume = payload.amount / float(price.sell_price)
    token = secrets.token_hex(4).upper()
    voucher = models.FuelVoucher(
        token=token, branch_id=payload.branch_id, shift_id=shift.id,
        fuel_type_id=payload.fuel_type_id, amount=payload.amount, volume=volume,
        payment_method=payload.payment_method, customer_id=payload.customer_id,
        issued_by=current_user.id)
    db.add(voucher); db.commit(); db.refresh(voucher)
    return {"id": voucher.id, "token": token, "status": voucher.status,
            "amount": float(voucher.amount), "volume": float(voucher.volume),
            "fuel_type_id": voucher.fuel_type_id, "shift_id": voucher.shift_id}


@router.get("/vouchers/{token}")
def get_voucher(token: str, db: Session = Depends(get_db),
                current_user: models.User = Depends(auth.get_current_user)):
    voucher = db.query(models.FuelVoucher).filter(models.FuelVoucher.token == token.upper()).first()
    if not voucher:
        raise HTTPException(status_code=404, detail="Voucher not found")
    auth.verify_branch_access(current_user, voucher.branch_id, db)
    return {"token": voucher.token, "status": voucher.status,
            "amount": float(voucher.amount), "volume": float(voucher.volume),
            "fuel_type_id": voucher.fuel_type_id, "shift_id": voucher.shift_id}


@router.post("/vouchers/{token}/redeem")
def redeem_voucher(token: str, payload: VoucherRedeem,
                   db: Session = Depends(get_db),
                   current_user: models.User = Depends(
                       auth.require_role("attendant", "supervisor", "manager", "developer"))):
    voucher = db.query(models.FuelVoucher).filter(
        models.FuelVoucher.token == token.upper()).with_for_update().first()
    if not voucher or voucher.status != "issued":
        raise HTTPException(status_code=409, detail="Voucher is invalid or already used")
    shift = db.query(models.Shift).get(voucher.shift_id)
    require_on_shift(db, shift, current_user)
    pump = db.query(models.FuelPump).get(payload.pump_id)
    tank = db.query(models.FuelTank).get(pump.tank_id) if pump else None
    if (not pump or not tank or pump.branch_id != voucher.branch_id
            or tank.fuel_type_id != voucher.fuel_type_id):
        raise HTTPException(status_code=403, detail="Select a pump at this branch for this fuel")
    if current_user.role == "attendant":
        # Attendants only dispense through pumps assigned to them this shift.
        assignment = db.query(models.ShiftPumpAssignment).filter_by(
            shift_id=shift.id, pump_id=pump.id, user_id=current_user.id).first()
        if not assignment:
            raise HTTPException(status_code=403, detail="Select one of your assigned pumps for this fuel")
    else:
        auth.verify_branch_access(current_user, voucher.branch_id, db)
    price = db.query(models.BranchFuelPrice).get((voucher.branch_id, voucher.fuel_type_id))
    sale = models.Sale(branch_id=voucher.branch_id, fuel_type_id=voucher.fuel_type_id,
        customer_id=voucher.customer_id, shift_id=shift.id, pump_id=pump.id,
        volume=voucher.volume, cost_price=price.current_cost, sell_price=price.sell_price,
        payment_method=voucher.payment_method, served_by=current_user.id,
        entry_source="voucher", notes=f"Voucher {voucher.token}")
    db.add(sale); db.flush()
    # Voucher redemption is the moment fuel is dispensed, so advance the
    # pump's automatic totaliser only after its sale row is created.
    db.query(models.FuelPump).filter_by(id=pump.id).update(
        {
            models.FuelPump.meter_reading:
                models.FuelPump.meter_reading + Decimal(str(voucher.volume))
        },
        synchronize_session=False,
    )
    voucher.status = "redeemed"; voucher.redeemed_by = current_user.id
    voucher.pump_id = pump.id; voucher.sale_id = sale.id; voucher.redeemed_at = datetime.now().astimezone()
    db.commit()
    return {"token": voucher.token, "status": voucher.status, "sale_id": sale.id,
            "pump_id": pump.id, "volume": float(voucher.volume)}
