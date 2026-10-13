"""Alembic environment. Migrations run as the schema owner, one transaction per migration."""

from alembic import context
from sqlalchemy import create_engine, pool

if context.is_offline_mode():
    raise RuntimeError(
        "Offline (SQL script) migrations are not supported; run `agentforge migrate`."
    )

url = context.config.get_main_option("sqlalchemy.url")
if not url:
    raise RuntimeError("sqlalchemy.url is not set; run migrations through `agentforge migrate`.")

engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
