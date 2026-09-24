from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(tags=["companies & branches"])


# ---------------------------------------------------------------------
# Companies. Creating a company is the one action that happens before
# anyone can log in, so it's intentionally public — think "sign up your
# fuel business". Everything else in the API requires a token that
# belongs to a company.
# ---------------------------------------------------------------------
@router.post("/companies", response_model=schemas.CompanyOut)
def create_company(payload: schemas.CompanyCreate, db: Session = Depends(get_db)):
    """Registers a new company/tenant (and seeds its default 'Fuel Purchases'
    expense category via the fn_create_company() database function). After
    this, call POST /companies/{company_id}/bootstrap-admin once to create
    that company's first login."""
    if db.query(models.Company).filter(models.Company.slug == payload.slug).first():
        raise HTTPException(status_code=400, detail="That company slug is already taken")

    row = db.execute(
        text(
            "SELECT fn_create_company(:name, :slug, :address, :lat, :lng, :phone, :email, :url) AS id"
        ),
        {
            "name": payload.name, "slug": payload.slug, "address": payload.address,
            "lat": payload.latitude, "lng": payload.longitude,
            "phone": payload.phone, "email": payload.email, "url": payload.url,
        },
    ).mappings().first()
    db.commit()

    company = db.query(models.Company).get(row["id"])
    return company


@router.get("/companies/by-slug/{slug}", response_model=schemas.CompanyOut)
def get_company_by_slug(slug: str, db: Session = Depends(get_db)):
    """Public lookup — e.g. for a 'sign in to <company>' screen before login."""
    company = db.query(models.Company).filter(models.Company.slug == slug).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/companies/me", response_model=schemas.CompanyOut)
def get_my_company(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    return db.query(models.Company).get(current_user.company_id)


@router.post("/companies/{company_id}/bootstrap-admin", response_model=schemas.UserOut)
def bootstrap_admin(
    company_id: int,
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
):
    """One-time, unauthenticated: creates the FIRST user (forced role
    'developer') for a brand-new company. Only works while that company has
    zero users — once any user exists, use POST /users (authenticated)
    instead."""
    company = db.query(models.Company).get(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    existing = db.query(models.User).filter(models.User.company_id == company_id).count()
    if existing > 0:
        raise HTTPException(status_code=400, detail="This company already has users — use POST /users instead")

    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    user = models.User(
        company_id=company_id,
        branch_id=None,
        username=payload.username,
        password_hash=auth.hash_password(payload.password),
        full_name=payload.full_name,
        role="developer",
        phone=payload.phone,
        email=payload.email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------
# Branches. Each company manages its own branches.
# ---------------------------------------------------------------------
@router.post("/branches", response_model=schemas.BranchOut)
def create_branch(
    payload: schemas.BranchCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("director", "developer")),
):
    if db.query(models.CompanyBranch).filter(
        models.CompanyBranch.company_id == current_user.company_id,
        models.CompanyBranch.branch_name == payload.branch_name,
    ).first():
        raise HTTPException(status_code=400, detail="A branch with that name already exists")

    branch = models.CompanyBranch(company_id=current_user.company_id, **payload.model_dump())
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


@router.get("/branches", response_model=List[schemas.BranchOut])
def list_branches(
    active_only: bool = True,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Branches of the logged-in user's own company only."""
    q = db.query(models.CompanyBranch).filter(models.CompanyBranch.company_id == current_user.company_id)
    if active_only:
        q = q.filter(models.CompanyBranch.is_active.is_(True))
    return q.order_by(models.CompanyBranch.branch_name).all()


@router.patch("/branches/{branch_id}/deactivate", response_model=schemas.BranchOut)
def deactivate_branch(
    branch_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("director", "developer")),
):
    branch = db.query(models.CompanyBranch).get(branch_id)
    if not branch or branch.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Branch not found in your company")
    branch.is_active = False
    db.commit()
    db.refresh(branch)
    return branch
