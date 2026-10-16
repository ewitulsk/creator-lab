"""Login, logout, current user and password change."""

from flask_login import current_user, login_user, logout_user

from ..auth import public
from ..extensions import db, limiter
from ..models import User
from . import ApiError, body, bp

MIN_PASSWORD = 10


@bp.post("/auth/login")
@public
@limiter.limit("10 per minute; 50 per hour")
def login():
    data = body()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    user = db.session.scalar(db.select(User).where(User.username == username))
    if user is None or not user.check_password(password):
        raise ApiError("wrong username or password", 401)
    login_user(user, remember=bool(data.get("remember", True)))
    return {"user": user.to_dict()}


@bp.post("/auth/logout")
@public
def logout():
    logout_user()
    return {"ok": True}


@bp.get("/auth/me")
@public
def me():
    if not current_user.is_authenticated:
        raise ApiError("not logged in", 401)
    return {"user": current_user.to_dict()}


@bp.post("/auth/password")
@limiter.limit("10 per hour")
def change_password():
    data = body()
    if not current_user.check_password(str(data.get("current_password", ""))):
        raise ApiError("current password is wrong", 403)
    new = str(data.get("new_password", ""))
    if len(new) < MIN_PASSWORD:
        raise ApiError(f"new password must be at least {MIN_PASSWORD} characters")
    current_user.set_password(new)
    db.session.commit()
    return {"ok": True}
