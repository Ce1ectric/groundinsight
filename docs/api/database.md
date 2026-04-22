# Database

SQLAlchemy-based CRUD helpers for persisting bus types, branch types and
entire networks to a SQLite database. The ORM mirror classes live next to the
Pydantic models in `groundinsight.models.database_models` and expose
`from_pydantic` / `to_pydantic` converters.

## CRUD functions

::: groundinsight.database.crud

## ORM models

::: groundinsight.models.database_models
