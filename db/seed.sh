#!/usr/bin/env bash
# Loads db/seeds/*.sql — DEVELOPMENT FIXTURES ONLY (synthetic stat lines).
# Never run against a production database; the ingest pipeline is the only
# production data path.
set -euo pipefail
cd "$(dirname "$0")"

DATABASE_URL="${DATABASE_URL:-postgres://cdlhub:cdlhub@localhost:54329/cdlhub}"
PSQL=(docker compose -f ../docker-compose.yml exec -T db psql -v ON_ERROR_STOP=1 -q -U cdlhub -d cdlhub)
if command -v psql >/dev/null 2>&1; then
  PSQL=(psql -v ON_ERROR_STOP=1 -q "$DATABASE_URL")
fi

for f in seeds/*.sql; do
  echo "seed  $(basename "$f")"
  "${PSQL[@]}" -f - < "$f"
done
echo "seeds loaded"
