from sqlalchemy import engine_from_config, pool

from agenda_first_notes.models import Base
from alembic import context

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(connection=connectable, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
