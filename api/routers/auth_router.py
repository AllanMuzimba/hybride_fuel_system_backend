from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Flutter should POST x-www-form-urlencoded: username, password."""
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.password_hash):
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
