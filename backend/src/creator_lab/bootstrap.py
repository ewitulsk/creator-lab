"""First-start setup: platforms, analyzer registry and the admin user."""

from __future__ import annotations

import logging

from .config import Config, is_set
from .extensions import db
from .models import Platform, User

log = logging.getLogger(__name__)

PLATFORMS = [("youtube", "YouTube")]


def ensure_platforms() -> None:
    for slug, name in PLATFORMS:
        if db.session.scalar(db.select(Platform).where(Platform.slug == slug)) is None:
            db.session.add(Platform(slug=slug, name=name))
    db.session.commit()


def ensure_admin(cfg: Config) -> User | None:
    """Create the admin from ADMIN_PASSWORD once. After that the stored hash wins,
    and the password is changed in Settings, not in .env."""
    existing = db.session.scalar(db.select(User).limit(1))
    if existing is not None:
        return existing
    if not is_set(cfg.admin_password):
        log.warning("No users yet and ADMIN_PASSWORD is not set in backend/.env; nobody can log in.")
        return None
    user = User(username=cfg.admin_username)
    user.set_password(cfg.admin_password)
    db.session.add(user)
    db.session.commit()
    log.info("Created admin user %r from ADMIN_PASSWORD.", user.username)
    return user


def bootstrap(cfg: Config) -> None:
    ensure_platforms()
    from .analyzers import ensure_analyzers

    ensure_analyzers()
    ensure_admin(cfg)
