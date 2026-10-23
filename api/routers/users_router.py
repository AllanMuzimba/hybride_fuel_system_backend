import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/users", tags=["users"])
PLATFORM_ROLES = {"developer", "dev_support"}
ROLE_MANAGERS = PLATFORM_ROLES | {"director"}


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("263") and len(digits) == 12:
        digits = "0" + digits[3:]
    return digits or None


def _check_contact_uniqueness(db: Session, email: Optional[str], phone: Optional[str], exclude_id: Optional[int] = None) -> None:
    email = email.strip().lower() if email else None
    if email and db.query(models.User).filter(
        func.lower(models.User.email) == email,
        models.User.id != (exclude_id or -1),
    ).first():
        raise HTTPException(status_code=409, detail="Email address is already in use")
    digits = _normalize_phone(phone)
    if digits and db.query(models.User).filter(
        func.regexp_replace(models.User.phone, r"[^0-9]", "", "g").in_((digits, "263" + digits[1:] if digits.startswith("0") else digits)),
        models.User.id != (exclude_id or -1),
    ).first():
        raise HTTPException(status_code=409, detail="Phone number is already in use")


def _visible_user(db: Session, user_id: int, current_user: models.User) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role not in PLATFORM_ROLES and user.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="User not found in your company")
    return user


@router.post("", response_model=schemas.UserOut, status_code=201)
def create_user(
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer", "dev_support")),
):
    platform = current_user.role in PLATFORM_ROLES
    company_id = payload.company_id if platform and payload.company_id is not None else current_user.company_id
    if payload.company_id is not None and not platform and payload.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="You can only create users in your company")
    if payload.role in PLATFORM_ROLES and current_user.role != "developer":
        raise HTTPException(status_code=403, detail="Only a developer can create platform users")
    if payload.role == "director" and current_user.role not in ROLE_MANAGERS:
        raise HTTPException(status_code=403, detail="Only a company director or platform manager can assign director role")
    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    _check_contact_uniqueness(db, payload.email, payload.phone)

    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company or not company.is_active:
        raise HTTPException(status_code=404, detail="Active company not found")
    if payload.branch_id is not None:
        branch = db.query(models.CompanyBranch).filter(models.CompanyBranch.id == payload.branch_id).first()
        if not branch or branch.company_id != company_id:
            raise HTTPException(status_code=404, detail="Branch not found in the selected company")

    user = models.User(
        company_id=company_id,
        branch_id=payload.branch_id,
        username=payload.username.strip(),
        password_hash=auth.hash_password(payload.password),
        full_name=payload.full_name.strip(),
        role=payload.role,
        phone=payload.phone.strip() if payload.phone else None,
        email=payload.email.strip().lower() if payload.email else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("", response_model=List[schemas.UserOut])
def list_users(
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("supervisor", "manager", "director", "developer", "dev_support")),
):
    query = db.query(models.User)
    if current_user.role in PLATFORM_ROLES:
        if company_id is not None:
            query = query.filter(models.User.company_id == company_id)
    else:
        query = query.filter(models.User.company_id == current_user.company_id)
    return query.order_by(models.User.company_id, models.User.full_name).limit(2000).all()


@router.patch("/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    payload: schemas.UserUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer", "dev_support")),
):
    target = _visible_user(db, user_id, current_user)
    if target.role in PLATFORM_ROLES and current_user.role != "developer":
        raise HTTPException(status_code=403, detail="Only a developer can manage platform accounts")
    changes = payload.model_dump(exclude_unset=True)
    new_password = changes.pop("new_password", None)
    requested_role = changes.get("role")
    if requested_role is not None:
        if current_user.role not in ROLE_MANAGERS:
            raise HTTPException(status_code=403, detail="Only a director or platform manager can change roles")
        if requested_role in PLATFORM_ROLES and current_user.role != "developer":
            raise HTTPException(status_code=403, detail="Only a developer can assign platform roles")
        if current_user.role == "director" and target.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="A director can only change roles in their own company")
    if "company_id" in changes and changes["company_id"] != target.company_id:
        if current_user.role not in PLATFORM_ROLES:
            raise HTTPException(status_code=403, detail="Only platform staff can move users between companies")
        company = db.query(models.Company).filter(
            models.Company.id == changes["company_id"], models.Company.is_active.is_(True)
        ).first()
        if not company:
            raise HTTPException(status_code=404, detail="Active company not found")
        if "branch_id" not in changes:
            changes["branch_id"] = None
    if "username" in changes and changes["username"] != target.username:
        if db.query(models.User).filter(
            models.User.username == changes["username"], models.User.id != target.id
        ).first():
            raise HTTPException(status_code=409, detail="Username already exists")
    if "email" in changes or "phone" in changes:
        _check_contact_uniqueness(
            db,
            changes.get("email", target.email),
            changes.get("phone", target.phone),
            exclude_id=target.id,
        )
    if "branch_id" in changes and changes["branch_id"] is not None:
        company_id = changes.get("company_id", target.company_id)
        branch = db.query(models.CompanyBranch).filter(
            models.CompanyBranch.id == changes["branch_id"],
            models.CompanyBranch.company_id == company_id,
        ).first()
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found in the selected company")
    if target.id == current_user.id and changes.get("is_active") is False:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")

    if new_password:
        target.password_hash = auth.hash_password(new_password)
    for key, value in changes.items():
        if key == "email":
            value = value.strip().lower() if value else None
        if key == "phone":
            value = value.strip() if value else None
        if key == "username" or key == "full_name":
            value = value.strip()
        setattr(target, key, value)
    db.commit()
    db.refresh(target)
    return target


@router.delete("/{user_id}", response_model=schemas.UserOut)
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer", "dev_support")),
):
    target = _visible_user(db, user_id, current_user)
    if target.role in PLATFORM_ROLES and current_user.role != "developer":
        raise HTTPException(status_code=403, detail="Only a developer can manage platform accounts")
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    target.is_active = False
    db.commit()
    db.refresh(target)
    return target
