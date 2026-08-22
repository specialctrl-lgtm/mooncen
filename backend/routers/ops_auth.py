"""Narrow authentication surface for the standalone Ops control API."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from backend import models
from backend.database import get_db
from backend.routers import auth


router = APIRouter(prefix="/auth", tags=["ops-auth"])


@router.post(
    "/ops/login",
    response_model=auth.AuthResponse,
    dependencies=[Depends(auth.rate_limit("ops-auth-login", 5, 60))],
)
def ops_login(
    payload: auth.OpsLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    return auth.ops_login(payload, response, db)


@router.post(
    "/logout",
    dependencies=[Depends(auth.rate_limit("auth-logout", 20, 60))],
)
def logout(
    response: Response,
    _user: models.User = Depends(auth.get_current_user),
):
    auth._clear_auth_cookies(response)
    return {"ok": True}
