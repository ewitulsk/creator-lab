"""The analyzer registry.

An analyzer is one versioned kind of derived data per video. Bumping a version
creates a new row, so old results stay attached to the version that made them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..extensions import db
from ..models import Analyzer


@dataclass(frozen=True)
class Spec:
    name: str
    version: str
    title: str
    runner_type: str  # builtin (computed in-app) | cpu | gpu
    description: str
    inputs: list[str] = field(default_factory=list)
    output_format: str = "json"
    applies_to: list[str] = field(default_factory=list)  # video formats; empty = all


REGISTRY: list[Spec] = []


def register(spec: Spec) -> Spec:
    REGISTRY.append(spec)
    return spec


def ensure_analyzers() -> None:
    names = {s.name for s in REGISTRY}
    for spec in REGISTRY:
        row = db.session.scalar(db.select(Analyzer).where(Analyzer.name == spec.name, Analyzer.version == spec.version))
        if row is None:
            row = Analyzer(name=spec.name, version=spec.version)
            db.session.add(row)
        row.title = spec.title
        row.description = spec.description
        row.inputs = list(spec.inputs)
        row.runner_type = spec.runner_type
        row.output_format = spec.output_format
        row.applies_to = list(spec.applies_to)
        row.is_current = True
    # Older versions of a registered analyzer stay in the table but are not current.
    current = {(s.name, s.version) for s in REGISTRY}
    for row in db.session.scalars(db.select(Analyzer).where(Analyzer.name.in_(names))):
        if (row.name, row.version) not in current:
            row.is_current = False
    db.session.commit()


def load_builtin_specs() -> None:
    """Import the modules that register specs (kept here so import order is explicit)."""
    from . import builtin  # noqa: F401


def current(name: str) -> Analyzer:
    row = db.session.scalar(db.select(Analyzer).where(Analyzer.name == name, Analyzer.is_current))
    if row is None:
        raise KeyError(f"no current analyzer named {name!r}")
    return row


def by_ref(ref: str) -> Analyzer | None:
    """'name@version' (or bare 'name' for the current version)."""
    name, _, version = ref.partition("@")
    q = db.select(Analyzer).where(Analyzer.name == name)
    q = q.where(Analyzer.version == version) if version else q.where(Analyzer.is_current)
    return db.session.scalar(q)
