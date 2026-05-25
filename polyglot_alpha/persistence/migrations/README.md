# Alembic migrations

This project uses `SQLModel.metadata.create_all(engine)` via
`polyglot_alpha.persistence.init_db()` for SQLite (the default). Alembic is
installed for Postgres deployments.

To bootstrap Alembic against a Postgres `DATABASE_URL`:

```bash
alembic init polyglot_alpha/persistence/migrations
# then point env.py.target_metadata at SQLModel.metadata
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

For local development the default sqlite engine is created automatically on
FastAPI startup via the lifespan hook in `polyglot_alpha.api.main`.
