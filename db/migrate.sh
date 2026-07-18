#!/usr/bin/env bash
# Applies db/migrations/*.sql in filename order, tracking applied files in
# schema_migrations. Forward-only, idempotent. Uses DATABASE_URL (falls back
# to the docker-compose default).
set -euo pipefail
cd "$(dirname "$0")"

DATABASE_URL="${DATABASE_URL:-postgres://cdlhub:cdlhub@localhost:54329/cdlhub}"
PSQL=(docker compose -f ../docker-compose.yml exec -T db psql -v ON_ERROR_STOP=1 -q -U cdlhub -d cdlhub)
# If psql is installed locally, prefer it (works against any DATABASE_URL, e.g. Neon).
if command -v psql >/dev/null 2>&1; then
  PSQL=(psql -v ON_ERROR_STOP=1 -q "$DATABASE_URL")
fi

"${PSQL[@]}" -c "CREATE TABLE IF NOT EXISTS schema_migrations (filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());"

for f in migrations/*.sql; do
  name="$(basename "$f")"
  applied="$("${PSQL[@]}" -tA -c "SELECT 1 FROM schema_migrations WHERE filename = '$name'")"
  if [ "$applied" = "1" ]; then
    echo "skip  $name"
  else
    echo "apply $name"
    "${PSQL[@]}" -f - < "$f"
    "${PSQL[@]}" -c "INSERT INTO schema_migrations (filename) VALUES ('$name');"
  fi
done
echo "migrations up to date"
