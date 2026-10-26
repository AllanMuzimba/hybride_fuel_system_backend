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
    current_user: models.User = Depends(auth.require_role("developer", "dev_support")),
):
    """Registers a new company/tenant (and seeds its default 'Fuel Purchases'
    expense category via the fn_create_company() database function). After
    this, call POST /companies/{company_id}/bootstrap-admin once to create
    that company's first login.

    A developer registers a company that works straight away
    (is_active=True, needs_confirmation=False); a dev_support registration is
    only an application — it starts inactive with needs_confirmation=True and
    registered_by set to the dev_support user, and a developer must confirm it
    (POST /companies/{company_id}/confirm) before it can operate. A developer
    records themselves in registered_by too.

    Branches: pass branch_names to name them up front (entries are trimmed,
    blank ones dropped, a duplicate name for this company is rejected with
    400); when that's absent or empty the branch_count default names
    ("Branch 1", "Branch 2", ...) are created as before.

    The slug is optional: when it is blank it is derived from the company
    name and made unique with a numeric suffix."""
    slug = (payload.slug or "").strip().lower()
    if slug:
        if db.query(models.Company).filter(models.Company.slug == slug).first():
            raise HTTPException(status_code=400, detail="That company slug is already taken")
    else:
        base = re.sub(r"[^a-z0-9]+", "-", payload.name.strip().lower()).strip("-") or "fuel-company"
        slug = base[:90]
        suffix = 2
        while db.query(models.Company).filter_by(slug=slug).first():
            slug = f"{base[:88]}-{suffix}"
            suffix += 1

    branch_names: List[str] = []
    for entry in payload.branch_names or []:
        name = entry.strip()
        if not name:
            continue
        if name in branch_names:
            raise HTTPException(status_code=400, detail="A branch with that name already exists")
        branch_names.append(name)

    row = db.execute(
        text(
            "SELECT fn_create_company(:name, :slug, :address, :lat, :lng, :phone, :email, :url) AS id"
        ),
        {
            "name": payload.name, "slug": slug, "address": payload.address,
            "lat": payload.latitude, "lng": payload.longitude,
            "phone": payload.phone, "email": payload.email, "url": payload.url,
        },
    ).mappings().first()
    db.commit()

    company = db.query(models.Company).get(row["id"])
    company.registered_by = current_user.id
    if current_user.role != "developer":
        company.is_active = False
        company.needs_confirmation = True
    db.commit()

    if branch_names:
        db.add_all([
            models.CompanyBranch(company_id=company.id, branch_name=name,
                                 address=payload.address, phone=payload.phone)
            for name in branch_names
        ])
    else:
        db.add_all([
            models.CompanyBranch(company_id=company.id, branch_name=f"Branch {number}",
                                 address=payload.address, phone=payload.phone)
            for number in range(1, payload.branch_count + 1)
        ])
    db.commit()
    db.refresh(company)
    return company


@router.post("/companies/{company_id}/confirm", response_model=schemas.CompanyOut)
def confirm_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer")),
):
    """Developer sign-off for a company the dev_support team registered:
    activates it (is_active=True), clears needs_confirmation and stamps
    confirmed_at/confirmed_by. 404 when the company doesn't exist, 409 when
    it isn't waiting for confirmation."""
    company = db.query(models.Company).get(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if not company.needs_confirmation:
        raise HTTPException(status_code=409, detail="This company does not need confirmation")

    company.is_active = True
    company.needs_confirmation = False
    company.confirmed_at = datetime.now(timezone.utc)
    company.confirmed_by = current_user.id
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
    pick the company a new tank belongs to from this list.

    dev_support only ever sees confirmed companies (needs_confirmation=False),
    whatever query parameters are passed; the developer sees every company and
    still filters inactive ones out with active_only."""
    q = db.query(models.Company)
    if current_user.role != "developer":
        q = q.filter(models.Company.needs_confirmation.is_(False))
    if active_only:
        q = q.filter(models.Company.is_active.is_(True))
    return q.order_by(models.Company.name).all()


@router.patch("/companies/{company_id}", response_model=schemas.CompanyOut)
def update_company(
    company_id: int,
    payload: schemas.CompanyUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company or (
        current_user.role != "developer" and company.id != current_user.company_id
    ):
        raise HTTPException(status_code=404, detail="Company not found")
    fields = payload.model_dump(exclude_unset=True)
    if current_user.role != "developer":
        fields.pop("is_active", None)
    for field, value in fields.items():
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
    company = db.query(models.Company).get(current_user.company_id) if current_user.company_id else None
    if company is None:
        raise HTTPException(status_code=404, detail="No company is linked to this account")
    return company


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
def _branch_row_out(branch: models.CompanyBranch, company_name: Optional[str]) -> dict:
    """BranchOut payload with the owning company's name filled in — the shape
    every branch endpoint answers with."""
    data = schemas.BranchOut.model_validate(branch).model_dump()
    data["company_name"] = company_name
    return data


def _branch_out(db: Session, branch: models.CompanyBranch) -> dict:
    """Same payload for a branch that was loaded on its own: one extra query
    for the company's name."""
    company_name = db.query(models.Company.name).filter(
        models.Company.id == branch.company_id
    ).scalar()
    return _branch_row_out(branch, company_name)


def _branch_for_edit(db: Session, current_user: models.User, branch_id: int) -> models.CompanyBranch:
    """Resolve a branch that the caller wants to change: 404 when it doesn't
    exist, 404 for a manager pointing at another company's branch, while
    developer / dev_support may edit any tenant's branch (the same
    cross-company rule resolve_company_id applies to company_id)."""
    branch = db.query(models.CompanyBranch).get(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    if branch.company_id != current_user.company_id and current_user.role not in auth.PLATFORM_ROLES:
        raise HTTPException(status_code=404, detail="Branch not found in your company")
    return branch


@router.post("/branches", response_model=schemas.BranchOut)
def create_branch(
    payload: schemas.BranchCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Opens a branch in your company; developer may pass
    payload.company_id to open one for another tenant, everyone else is
    pinned to their own company."""
    target_company = auth.resolve_company_id(db, current_user, payload.company_id)
    if db.query(models.CompanyBranch).filter(
        models.CompanyBranch.company_id == target_company,
        models.CompanyBranch.branch_name == payload.branch_name,
    ).first():
        raise HTTPException(status_code=400, detail="A branch with that name already exists")

    fields = payload.model_dump()
    fields.pop("company_id", None)
    branch = models.CompanyBranch(company_id=target_company, **fields)
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return _branch_out(db, branch)


@router.get("/branches", response_model=List[schemas.BranchOut])
def list_branches(
    active_only: bool = True,
    company_id: Optional[int] = None,
    all_companies: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Branches of the logged-in user's own company only — pass company_id
    to list another tenant's branches (developer / dev_support only, used by
    the Tanks screen's company -> branch picker).

    all_companies=true returns every branch of every company and is reserved
    for developer / dev_support (403 otherwise). Both modes answer from a
    single join, so every row carries its company_name."""
    q = db.query(
        models.CompanyBranch, models.Company.name.label("company_name")
    ).join(models.Company, models.Company.id == models.CompanyBranch.company_id)
    if all_companies:
        if current_user.role not in auth.PLATFORM_ROLES:
            raise HTTPException(
                status_code=403,
                detail="Only a developer or dev_support account can list every company's branches",
            )
    else:
        target_company = auth.resolve_company_id(db, current_user, company_id)
        q = q.filter(models.CompanyBranch.company_id == target_company)
    if active_only:
        q = q.filter(models.CompanyBranch.is_active.is_(True))
    rows = q.order_by(models.Company.name, models.CompanyBranch.branch_name).all()
    return [_branch_row_out(branch, company_name) for branch, company_name in rows]


@router.patch("/branches/{branch_id}", response_model=schemas.BranchOut)
def update_branch(
    branch_id: int,
    payload: schemas.BranchUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Edit a branch: a company manager or director may change their own
    company's branches, a developer any tenant's. Only the fields present
    in the payload are applied, and a rename onto a name already used in
    that company is rejected with 400."""
    branch = _branch_for_edit(db, current_user, branch_id)
    fields = payload.model_dump(exclude_unset=True)
    if "branch_name" in fields:
        fields["branch_name"] = fields["branch_name"].strip()
        if not fields["branch_name"]:
            raise HTTPException(status_code=400, detail="Branch name must not be empty")
        if db.query(models.CompanyBranch).filter(
            models.CompanyBranch.company_id == branch.company_id,
            models.CompanyBranch.branch_name == fields["branch_name"],
            models.CompanyBranch.id != branch.id,
        ).first():
            raise HTTPException(status_code=400, detail="A branch with that name already exists")
    for field, value in fields.items():
        setattr(branch, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(branch)
    return _branch_out(db, branch)


@router.patch("/branches/{branch_id}/deactivate", response_model=schemas.BranchOut)
def deactivate_branch(
    branch_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Retires a branch (is_active=False). A manager or director can only
    deactivate their own company's branches; a developer may deactivate any
    tenant's."""
    branch = _branch_for_edit(db, current_user, branch_id)
    branch.is_active = False
    db.commit()
    db.refresh(branch)
    return _branch_out(db, branch)
