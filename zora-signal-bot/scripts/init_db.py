#!/usr/bin/env python
"""
scripts/init_db.py
─────────────────────────────────────────────────────────────────────────────
One-shot script to create all tables (without Alembic).
Use only for local dev bootstrapping.
For production, always use `alembic upgrade head`.

Run:
    python scripts/init_db.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db.base import Base, engine
from app.db import models  # noqa: F401 — register models
from app.logging_config import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


async def main() -> None:
    log.info("creating_tables")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("tables_created")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
