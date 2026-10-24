# Project

Backend for a restaurant ordering system built primarily to learn
production-oriented backend engineering.

## Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL
- Alembic
- Pytest

## Engineering principles

- Prefer simple solutions over premature architecture.
- Do not introduce new infrastructure without a concrete problem that requires it.
- Preserve database integrity with appropriate constraints.
- Treat PostgreSQL as the source of truth for persisted application state.
- Do not silently change database schemas.
- Use Alembic migrations for schema changes.
- Prefer Decimal/Numeric for monetary values.
- Do not trust prices or calculated totals sent by clients.

## Workflow

Before implementing a significant change:

1. Inspect the existing code.
2. State the files you expect to modify.
3. Identify assumptions or important design decisions.

After implementing:

1. Run relevant tests.
2. Report what changed.
3. Report any failed tests or unresolved issues.
4. Do not claim a guarantee that has not been tested or enforced.

## Learning goal

This project intentionally progresses from basic CRUD toward:
- data integrity
- transactions
- failure handling
- idempotency
- concurrency
- testing
- production reliability

Do not prematurely implement later-stage concepts unless explicitly requested.