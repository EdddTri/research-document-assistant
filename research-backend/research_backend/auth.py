import hashlib
import secrets
from dataclasses import dataclass

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from research_core.config import settings
from research_core.db import get_db
from research_core.models import LoginSession, RefreshToken, User, now

router = APIRouter()
hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=256)


@dataclass
class Principal:
    user: User
    session: LoginSession


def public_user(user):
    return {"id": user.id, "email": user.email, "role": user.role}


def current(request: Request, db: Session = Depends(get_db)) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Sign in to continue")
    cfg = settings()
    try:
        claims = jwt.decode(header[7:], cfg.jwt_secret, algorithms=["HS256"],
                            issuer=cfg.jwt_issuer, audience=cfg.jwt_audience,
                            options={"require": ["sub", "sid", "exp", "iss", "aud"]})
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired access token")
    login = db.get(LoginSession, claims["sid"])
    user = db.get(User, claims["sub"])
    if not login or not user or login.user_id != user.id or not user.active or login.revoked or login.expires_at <= now():
        raise HTTPException(401, "Session is no longer active")
    return Principal(user, login)


def admin(principal: Principal = Depends(current)):
    if principal.user.role != "admin":
        raise HTTPException(403, "Administrator access required")
    return principal


def access(login: LoginSession):
    cfg = settings()
    return jwt.encode({"sub": login.user_id, "sid": login.id,
                       "iat": now(), "exp": now() + cfg.access_minutes * 60,
                       "iss": cfg.jwt_issuer, "aud": cfg.jwt_audience}, cfg.jwt_secret, algorithm="HS256")


def set_cookies(response, token, csrf):
    options = dict(secure=settings().cookie_secure, samesite="lax", max_age=settings().session_days * 86400, path="/")
    response.set_cookie("research_refresh", token, httponly=True, **options)
    response.set_cookie("research_csrf", csrf, httponly=False, **options)
    response.headers["Cache-Control"] = "no-store"


def issue(db, request, response, user):
    # Replace the previous browser login, even if its refresh token was already used.
    previous = db.get(RefreshToken, digest(request.cookies.get("research_refresh", "")))
    if previous:
        old = db.get(LoginSession, previous.session_id)
        if old:
            old.revoked = True
    csrf, raw = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    login = LoginSession(user_id=user.id, expires_at=now() + settings().session_days * 86400,
                         csrf_hash=digest(csrf), user_agent=request.headers.get("user-agent", "")[:300])
    db.add(login)
    db.flush()
    db.add(RefreshToken(token_hash=digest(raw), session_id=login.id, expires_at=login.expires_at))
    db.commit()
    set_cookies(response, raw, csrf)
    return {"access_token": access(login), "user": public_user(user)}


@router.post("/auth/register", status_code=201)
def register(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    user = User(email=str(body.email).lower(), password_hash=hasher.hash(body.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with that email already exists")
    return issue(db, request, response, user)


@router.post("/auth/login")
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(body.email).lower()))
    try:
        hasher.verify(user.password_hash if user and user.password_hash else DUMMY_HASH, body.password)
    except VerificationError:
        raise HTTPException(401, "Invalid email or password")
    if not user or not user.active or not user.password_hash:
        raise HTTPException(401, "Invalid email or password")
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(body.password)
    return issue(db, request, response, user)


def cookie_session(db, request):
    token = db.get(RefreshToken, digest(request.cookies.get("research_refresh", "")))
    if not token:
        raise HTTPException(401, "No refresh session")
    # Serialize all rotations for a family; re-read the token after acquiring the lock.
    login = db.scalar(select(LoginSession).where(LoginSession.id == token.session_id).with_for_update())
    db.refresh(token)
    csrf = request.headers.get("x-csrf-token", "")
    if not login or not secrets.compare_digest(digest(csrf), login.csrf_hash):
        raise HTTPException(403, "Invalid CSRF token")
    if login.revoked or login.expires_at <= now() or token.expires_at <= now():
        raise HTTPException(401, "Session expired")
    return login, token


@router.post("/auth/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    login, token = cookie_session(db, request)
    if token.used:
        login.revoked = True
        db.commit()
        raise HTTPException(401, "Refresh token reuse detected; session revoked")
    user = db.get(User, login.user_id)
    if not user or not user.active:
        login.revoked = True
        db.commit()
        raise HTTPException(401, "Account unavailable")
    token.used = True
    raw = secrets.token_urlsafe(48)
    db.add(RefreshToken(token_hash=digest(raw), session_id=login.id, expires_at=login.expires_at))
    db.commit()
    set_cookies(response, raw, request.headers["x-csrf-token"])
    return {"access_token": access(login), "user": public_user(user)}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    login, _ = cookie_session(db, request)
    login.revoked = True
    db.commit()
    response.delete_cookie("research_refresh", path="/")
    response.delete_cookie("research_csrf", path="/")


@router.get("/users/me")
def me(principal: Principal = Depends(current)):
    return public_user(principal.user)


@router.get("/auth/sessions")
def sessions(principal: Principal = Depends(current), db: Session = Depends(get_db)):
    rows = db.scalars(select(LoginSession).where(LoginSession.user_id == principal.user.id,
                       LoginSession.revoked.is_(False), LoginSession.expires_at > now()))
    return [{"id": s.id, "created_at": s.created_at, "user_agent": s.user_agent,
             "current": s.id == principal.session.id} for s in rows]


@router.delete("/auth/sessions/{session_id}", status_code=204)
def revoke(session_id: str, principal: Principal = Depends(current), db: Session = Depends(get_db)):
    login = db.get(LoginSession, session_id)
    if not login or login.user_id != principal.user.id:
        raise HTTPException(404, "Session not found")
    login.revoked = True
    db.commit()


@router.post("/auth/logout-all", status_code=204)
def logout_all(principal: Principal = Depends(current), db: Session = Depends(get_db)):
    for login in db.scalars(select(LoginSession).where(LoginSession.user_id == principal.user.id)):
        login.revoked = True
    db.commit()
