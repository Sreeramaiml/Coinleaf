"""Alembic environment. Migrations always run on a connection handed in by
finassist.db.migrate (which holds the SQLCipher key); there is no URL in a config file."""

from alembic import context

from finassist.db.schema import metadata

connection = context.config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=metadata,
    render_as_batch=True,
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
