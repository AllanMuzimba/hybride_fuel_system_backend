"""
Authentication: password hashing + JWT issuing/verification, and a
role-based dependency (`require_role`) that routers use to restrict
sensitive endpoints (e.g. only manager/director/developer can edit
fuel prices or register suppliers).
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from . import models
from .database import get_db

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-secret-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))  # 8-hour shift

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_minutes: Optional[int] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or ACCESS_TOKEN_EXPIRE_MINUTES
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_role(*allowed_roles: Iterable[str]):
    """Usage: Depends(require_role("manager", "director", "developer"))"""
    def _checker(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' is not permitted to perform this action",
            )
        return user
    return _checker


# ------------------------------------------------------------ tenancy ----
# Every user belongs to exactly one company, and optionally one branch
# (NULL branch_id = access to every branch in their company — typically
# director/developer). These helpers keep that boundary enforced in one
# place instead of repeating it in every router.

def resolve_branch_id(
    db: Session, current_user: models.User, branch_id: Optional[int] = None
) -> int:
    """Figure out which branch a branch-scoped write (a sale, a receipt, an
    expense, ...) belongs to:
      - if the caller passed a branch_id, it must belong to their company
      - otherwise fall back to the user's own assigned branch
      - if neither is available, the caller must say which branch they mean
    """
    if branch_id is not None:
        branch = db.query(models.CompanyBranch).get(branch_id)
        if not branch or branch.company_id != current_user.company_id:
            raise HTTPException(status_code=404, detail="Branch not found")
        return branch_id

    if current_user.branch_id is not None:
        return current_user.branch_id

    raise HTTPException(
        status_code=400,
        detail="branch_id is required (your account isn't tied to a single branch)",
    )


def verify_branch_in_company(db: Session, current_user: models.User, branch_id: int) -> models.CompanyBranch:
    branch = db.query(models.CompanyBranch).get(branch_id)
    if not branch or branch.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Branch not found")
    return branch


def verify_owned_by_company(current_user: models.User, resource) -> None:
    """Raise 404 if `resource` (must have a .company_id) doesn't belong to
    the caller's company — used so one tenant can never see another's rows,
    even by guessing an id."""
    if resource is None or resource.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Not found")


def verify_branch_access(user: models.User, branch_id: int, db: Session) -> models.CompanyBranch:
    """Raise 403/404 unless `branch_id` is a real branch of the user's own
    company, and — if the user is tied to one specific branch (branch_id is
    set on their user row) — that it's their branch. Call this in any
    endpoint that writes branch-scoped data (receipts, sales, stock, etc.)."""
    branch = db.query(models.CompanyBranch).get(branch_id)
    if not branch or branch.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="Branch not found in your company")
    if user.branch_id is not None and user.branch_id != branch_id:
        raise HTTPException(status_code=403, detail="You are not assigned to this branch")
    return branch
