#!/bin/sh
set -eu

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-sub2api-postgres}"
MIGRATIONS_DIR="${1:-db/migrations}"
CHECK_DATABASE="paper_insight_migration_check_$$"
DATABASE_CREATED=0

cleanup() {
  if [ "$DATABASE_CREATED" -eq 1 ]; then
    docker exec "$POSTGRES_CONTAINER" dropdb --if-exists -U "$POSTGRES_USER" "$CHECK_DATABASE" >/dev/null
  fi
}

trap cleanup EXIT INT TERM

if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "Migration directory not found: $MIGRATIONS_DIR" >&2
  exit 1
fi

POSTGRES_USER="$(docker exec "$POSTGRES_CONTAINER" printenv POSTGRES_USER)"
if [ -z "$POSTGRES_USER" ]; then
  echo "POSTGRES_USER is not available in container $POSTGRES_CONTAINER" >&2
  exit 1
fi

EXISTING_DATABASE="$(
  docker exec "$POSTGRES_CONTAINER" \
    psql -U "$POSTGRES_USER" -d postgres -Atqc \
    "SELECT 1 FROM pg_database WHERE datname = '$CHECK_DATABASE'"
)"
if [ -n "$EXISTING_DATABASE" ]; then
  echo "Refusing to reuse existing database: $CHECK_DATABASE" >&2
  exit 1
fi

docker exec "$POSTGRES_CONTAINER" createdb -U "$POSTGRES_USER" "$CHECK_DATABASE"
DATABASE_CREATED=1

MIGRATION_COUNT=0
for migration in "$MIGRATIONS_DIR"/*.sql; do
  if [ ! -f "$migration" ]; then
    echo "No SQL migrations found in $MIGRATIONS_DIR" >&2
    exit 1
  fi
  docker exec -i "$POSTGRES_CONTAINER" \
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$CHECK_DATABASE" \
    < "$migration" >/dev/null
  MIGRATION_COUNT=$((MIGRATION_COUNT + 1))
done

TABLE_COUNT="$(
  docker exec "$POSTGRES_CONTAINER" \
    psql -U "$POSTGRES_USER" -d "$CHECK_DATABASE" -Atqc \
    "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public'"
)"
ZOTERO_TABLE_COUNT="$(
  docker exec "$POSTGRES_CONTAINER" \
    psql -U "$POSTGRES_USER" -d "$CHECK_DATABASE" -Atqc \
    "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'zotero_%'"
)"

echo "Applied $MIGRATION_COUNT migrations in PostgreSQL container $POSTGRES_CONTAINER"
echo "Created $TABLE_COUNT public tables, including $ZOTERO_TABLE_COUNT Zotero tables"
