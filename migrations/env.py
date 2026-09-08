from alembic import context
from sqlalchemy import create_engine

from mae.config import Settings

settings = Settings()
settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
engine = create_engine(f"sqlite:///{settings.state_dir / 'engine.db'}")
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
