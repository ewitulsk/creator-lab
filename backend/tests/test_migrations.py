from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade

from creator_lab import create_app
from creator_lab.extensions import db

from .conftest import make_config


def test_migrations_match_models(tmp_path):
    """The migration chain must build exactly the schema the models describe."""
    app = create_app(make_config(tmp_path))
    with app.app_context():
        upgrade()
        with db.engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
            diff = compare_metadata(ctx, db.metadata)
        db.engine.dispose()
    assert diff == [], diff
