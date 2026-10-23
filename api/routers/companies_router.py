import re
from datetime import datetime, timezone
from typing import List, Optional
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
def create_company(
    payload: schemas.CompanyCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer")),
):
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
    db.add_all([
        models.CompanyBranch(company_id=company.id, branch_name=f"Branch {number}",
                             address=payload.address, phone=payload.phone)
        for number in range(1, payload.branch_count + 1)
    ])
    db.commit()
    db.refresh(company)
    return company


@router.post("/companies/registration-requests", response_model=schemas.CompanyRegistrationRequestOut, status_code=201)
def submit_company_registration(
    payload: schemas.CompanyRegistrationRequestCreate,
    db: Session = Depends(get_db),
):
    """Public company application; a developer must approve before a tenant exists."""
    request = models.CompanyRegistrationRequest(
        company_name=payload.company_name.strip(), address=payload.address.strip(),
        branch_count=payload.branch_count, phone=payload.phone.strip(),
        email=payload.email.strip(), message=payload.message.strip(),
        status="pending",
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


@router.get("/companies/registration-requests", response_model=List[schemas.CompanyRegistrationRequestOut])
def list_company_registration_requests(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer", "dev_support")),
):
    query = db.query(models.CompanyRegistrationRequest)
    if status and status != "all":
        query = query.filter(models.CompanyRegistrationRequest.status == status)
    return query.order_by(models.CompanyRegistrationRequest.created_at.desc()).limit(500).all()


@router.post("/companies/registration-requests/{request_id}/approve", response_model=schemas.CompanyOut)
def approve_company_registration(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer")),
):
    """Create a tenant and its requested branches only after developer approval."""
    request = db.query(models.CompanyRegistrationRequest).filter_by(id=request_id).with_for_update().first()
    if request is None:
        raise HTTPException(status_code=404, detail="Registration request not found")
    if request.status != "pending":
        raise HTTPException(status_code=409, detail="This request has already been reviewed")
    base = re.sub(r"[^a-z0-9]+", "-", request.company_name.lower()).strip("-") or "fuel-company"
    slug = base[:90]
    suffix = 2
    while db.query(models.Company).filter_by(slug=slug).first():
        slug = f"{base[:88]}-{suffix}"
        suffix += 1
    row = db.execute(text(
        "SELECT fn_create_company(:name, :slug, :address, NULL, NULL, :phone, :email, NULL) AS id"
    ), {"name": request.company_name, "slug": slug, "address": request.address,
        "phone": request.phone, "email": request.email}).mappings().first()
    company_id = row["id"]
    db.add_all([
        models.CompanyBranch(company_id=company_id, branch_name=f"Branch {number}",
                             address=request.address, phone=request.phone)
        for number in range(1, request.branch_count + 1)
    ])
    request.status = "approved"
    request.company_id = company_id
    request.reviewed_by = current_user.id
    request.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return db.query(models.Company).get(company_id)


@router.get("/companies", response_model=List[schemas.CompanyOut])
def list_companies(
    active_only: bool = True,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer", "dev_support")),
):
    """Every tenant in the system — the developer and their dev_support team
    pick the company a new tank belongs to from this list."""
    q = db.query(models.Company)
    if active_only:
        q = q.filter(models.Company.is_active.is_(True))
    return q.order_by(models.Company.name).all()


@router.patch("/companies/{company_id}", response_model=schemas.CompanyOut)
def update_company(
    company_id: int,
    payload: schemas.CompanyUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer")),
):
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(company)
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
    current_user: models.User = Depends(auth.require_role("developer", "dev_support")),
):
    """One-time support setup: create the approved company's first manager."""
    company = db.query(models.Company).get(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if not company.is_active:
        raise HTTPException(status_code=403, detail="Company registration has not been approved")

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
        role="manager",
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
    current_user: models.User = Depends(auth.require_role("manager", "developer")),
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
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Branches of the logged-in user's own company only — pass company_id
    to list another tenant's branches (developer / dev_support only, used by
    the Tanks screen's company -> branch picker)."""
    target_company = auth.resolve_company_id(db, current_user, company_id)
    q = db.query(models.CompanyBranch).filter(models.CompanyBranch.company_id == target_company)
    if active_only:
        q = q.filter(models.CompanyBranch.is_active.is_(True))
    return q.order_by(models.CompanyBranch.branch_name).all()


@router.patch("/branches/{branch_id}/deactivate", response_model=schemas.BranchOut)
def deactivate_branch(
    branch_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "developer")),
):
    branch = db.query(models.CompanyBranch).get(branch_id)
    if not branch or branch.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Branch not found in your company")
    branch.is_active = False
    db.commit()
    db.refresh(branch)
    return branch
