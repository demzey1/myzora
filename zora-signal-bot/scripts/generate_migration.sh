#!/usr/bin/env bash
# scripts/generate_migration.sh
# Generate a new Alembic migration based on model changes.
# Usage: ./scripts/generate_migration.sh "describe your change"

set -euo pipefail

MSG="${1:-auto_migration}"

echo "Generating migration: $MSG"
docker compose exec api alembic revision --autogenerate -m "$MSG"
echo "Done. Review migrations/versions/ before applying."
