from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import models, schemas, auth, email_service
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Accept a username, email address, or phone number as the login ID."""
    identity = form_data.username.strip()
    digits = re.sub(r"\D", "", identity)
    phone_values = {digits}
    if digits.startswith("263") and len(digits) == 12:
        phone_values.add("0" + digits[3:])
    elif digits.startswith("0") and len(digits) == 10:
        phone_values.add("263" + digits[1:])
    phone_match = func.regexp_replace(models.User.phone, r"[^0-9]", "", "g").in_(phone_values)
    candidates = db.query(models.User).filter(or_(
        models.User.username == identity,
        func.lower(models.User.email) == identity.lower(),
        phone_match if len(digits) >= 7 else False,
    )).all()
    user = next((candidate for candidate in candidates
                 if auth.verify_password(form_data.password, candidate.password_hash)), None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    token = auth.create_access_token(
        {"sub": str(user.id), "role": user.role, "company_id": user.company_id, "branch_id": user.branch_id}
    )
    return schemas.Token(
        access_token=token, role=user.role, user_id=user.id, full_name=user.full_name,
        company_id=user.company_id, branch_id=user.branch_id,
    )


@router.get("/me", response_model=schemas.UserOut)
def read_me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


@router.patch("/me", response_model=schemas.UserOut)
def update_me(
    payload: schemas.ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    changes = payload.model_dump(exclude_unset=True)
    new_password = changes.pop("new_password", None)
    current_password = changes.pop("current_password", None)
    if new_password:
        if not current_password or not auth.verify_password(current_password, current_user.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        current_user.password_hash = auth.hash_password(new_password)

    if "email" in changes:
        email = (changes["email"] or "").strip().lower() or None
        if email and "@" not in email:
            raise HTTPException(status_code=400, detail="Enter a valid email address")
        conflict = db.query(models.User).filter(
            func.lower(models.User.email) == email,
            models.User.id != current_user.id,
        ).first() if email else None
        if conflict:
            raise HTTPException(status_code=409, detail="That email address is already in use")
        changes["email"] = email
    if "phone" in changes:
        phone = (changes["phone"] or "").strip() or None
        digits = re.sub(r"\D", "", phone or "")
        if digits.startswith("263") and len(digits) == 12:
            digits = "0" + digits[3:]
        conflict = None
        if digits:
            phone_values = {digits}
            if digits.startswith("0") and len(digits) == 10:
                phone_values.add("263" + digits[1:])
            conflict = db.query(models.User).filter(
                func.regexp_replace(models.User.phone, r"[^0-9]", "", "g").in_(phone_values),
                models.User.id != current_user.id,
            ).first()
        if conflict:
            raise HTTPException(status_code=409, detail="That phone number is already in use")
        changes["phone"] = phone
    for key, value in changes.items():
        setattr(current_user, key, value)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/password-reset/request")
def request_password_reset(payload: schemas.PasswordResetRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    if not email_service.configured():
        raise HTTPException(status_code=503, detail="Password reset email is not configured yet")
    user = db.query(models.User).filter(func.lower(models.User.email) == email, models.User.is_active.is_(True)).first()
    if user:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        db.query(models.PasswordResetToken).filter(
            models.PasswordResetToken.user_id == user.id,
            models.PasswordResetToken.used_at.is_(None),
        ).delete(synchronize_session=False)
        token_row = models.PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(token_row)
        db.commit()
        try:
            email_service.send(user.email, user.full_name, raw_token)
        except Exception as exc:
            db.delete(token_row)
            db.commit()
            raise HTTPException(status_code=503, detail="Could not send reset email. Try again later.") from exc
    return {"message": "If an active account uses that email, a reset link has been sent."}


@router.post("/password-reset/complete")
def complete_password_reset(payload: schemas.PasswordResetComplete, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    token_row = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token_hash == token_hash,
        models.PasswordResetToken.used_at.is_(None),
        models.PasswordResetToken.expires_at > now,
    ).with_for_update().first()
    if not token_row:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")
    user = db.query(models.User).filter(models.User.id == token_row.user_id, models.User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")
    user.password_hash = auth.hash_password(payload.new_password)
    token_row.used_at = now
    db.commit()
    return {"message": "Password reset. You can now sign in with your new password."}
