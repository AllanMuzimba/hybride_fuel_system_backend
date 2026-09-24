from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=schemas.UserOut)
def create_user(
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("director", "developer")),
):
    """Creates a user in the CALLER's own company. If branch_id is given, it
    must be one of that company's own branches."""
    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    if payload.branch_id is not None:
        branch = db.query(models.CompanyBranch).get(payload.branch_id)
        if not branch or branch.company_id != current_user.company_id:
            raise HTTPException(status_code=404, detail="Branch not found in your company")

    user = models.User(
        company_id=current_user.company_id,
        branch_id=payload.branch_id,
        username=payload.username,
        password_hash=auth.hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        phone=payload.phone,
        email=payload.email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("", response_model=List[schemas.UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("supervisor", "manager", "director", "developer")),
):
    """Users of the caller's own company only."""
    return (
        db.query(models.User)
        .filter(models.User.company_id == current_user.company_id)
        .order_by(models.User.full_name)
        .all()
    )


@router.patch("/{user_id}/deactivate", response_model=schemas.UserOut)
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("director", "developer")),
):
    user = db.query(models.User).get(user_id)
    if not user or user.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="User not found in your company")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user
