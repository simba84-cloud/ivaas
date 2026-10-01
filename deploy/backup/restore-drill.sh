#!/usr/bin/env bash
# The restore drill (proposal T10.2): prove the newest backup restores, and how long
# it took, before anyone needs it to.
#
#   ./deploy/backup/restore-drill.sh [path/to/ivaas-<stamp>.dump]
#
# The dump is restored into a throwaway Postgres with no network, so it cannot touch
# the real database; it is removed afterwards, with its data. The restored copy must
# match the backup's manifest exactly: the schema version, and every table's rows
# (counted in the same snapshot as the dump). Row-level security must come back on.
#
# Reported: RPO, how old the backup was (the data a disaster now would lose), and
# RTO, how long the restore took. Exit 1 if anything does not match.
set -euo pipefail
cd "$(dirname "$0")/../.."
DIR="${IVAAS_BACKUP_DIR:-./backups}/db"
DUMP="${1:-$(ls -1 "$DIR"/ivaas-*.dump 2>/dev/null | sort | tail -1)}"
[ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "no backup found in $DIR"; exit 2; }
BASE="${DUMP%.dump}"
MANIFEST="$BASE.json"
[ -f "$MANIFEST" ] || { echo "no manifest beside $DUMP"; exit 2; }
IMAGE="${IVAAS_DRILL_IMAGE:-timescale/timescaledb:latest-pg16}"
NAME="ivaas-restore-drill-$$"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "${IVAAS_BACKUP_DIR:-./backups}/drills"
REPORT="${IVAAS_BACKUP_DIR:-./backups}/drills/drill-$STAMP.md"
ABS_DIR=$(cd "$(dirname "$DUMP")" && pwd)

field() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$MANIFEST" "$1"; }
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "drill: $(basename "$DUMP"), taken $(field taken_at), schema $(field alembic_version)"
if [ "$(shasum -a 256 "$DUMP" | cut -d' ' -f1)" != "$(field sha256)" ]; then
  echo "FAIL: the dump's SHA-256 does not match its manifest"; exit 1
fi
started=$(date +%s)
# --network none: the throwaway server reaches nothing, and nothing reaches it
docker run -d --name "$NAME" --network none -e POSTGRES_USER=ivaas -e POSTGRES_PASSWORD=drill \
  -e POSTGRES_DB=ivaas -v "$ABS_DIR:/backup:ro" "$IMAGE" >/dev/null
q() { docker exec "$NAME" psql -X -q -At -U ivaas -d ivaas -v ON_ERROR_STOP=1 -c "$1"; }
for _ in $(seq 1 60); do
  # ready, and past the image's own first start (it restarts once after initdb)
  if docker exec "$NAME" pg_isready -U ivaas -d ivaas >/dev/null 2>&1 && q "SELECT 1" >/dev/null 2>&1 \
     && [ "$(docker logs "$NAME" 2>&1 | grep -c 'database system is ready to accept connections')" -ge 2 ]; then
    break
  fi
  sleep 1
done

ROLES="/backup/$(basename "$BASE").roles.sql"
if docker exec "$NAME" test -f "$ROLES"; then
  # the roles the schema grants to; "already exists" for ivaas itself is expected
  docker exec "$NAME" psql -X -q -U ivaas -d ivaas -f "$ROLES" >/dev/null 2>&1 || true
fi
q "SELECT timescaledb_pre_restore();" >/dev/null
set +e
docker exec "$NAME" pg_restore -U ivaas -d ivaas --no-owner --role=ivaas "/backup/$(basename "$DUMP")" \
  >"$REPORT.restore.log" 2>&1
restore_rc=$?
set -e
q "SELECT timescaledb_post_restore();" >/dev/null
restored=$(date +%s)

COUNTS="SELECT json_object_agg(table_name, (xpath('/row/c/text()', query_to_xml(
  format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text::bigint
  ORDER BY table_name) FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"
got_rows=$(q "$COUNTS")
got_version=$(q "SELECT version_num FROM alembic_version;")
unforced=$(q "SELECT coalesce(string_agg(c.relname, ', '), '') FROM pg_class c
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
  JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
  WHERE c.relkind = 'r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity);")
# the API works as ivaas_app under row-level security: its grants must come back as
# they were (it is not meant to read everything: alembic_version, for one)
readable=$(q "SELECT coalesce(json_agg(table_name ORDER BY table_name), '[]') FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  AND has_table_privilege('ivaas_app', format('public.%I', table_name), 'SELECT');" 2>/dev/null \
  || echo '"the role ivaas_app is missing"')
verified=$(date +%s)

python3 - "$MANIFEST" "$got_rows" "$got_version" "$unforced" "$readable" "$restore_rc" \
  "$((restored - started))" "$((verified - started))" "$REPORT" "$(basename "$DUMP")" <<'PY'
import json, sys
from datetime import datetime, timezone
manifest, rows, version, unforced, readable, rc, restore_s, total_s, report, dump = sys.argv[1:]
m = json.load(open(manifest))
got = json.loads(rows or "{}")
want = m["rows"]
taken = datetime.fromisoformat(m["taken_at"].replace("Z", "+00:00"))
rpo = (datetime.now(timezone.utc) - taken).total_seconds() / 60
problems = []
if version != m["alembic_version"]:
    problems.append(f"schema {version!r} restored, {m['alembic_version']!r} backed up")
for table in sorted(set(want) | set(got)):
    if want.get(table) != got.get(table):
        problems.append(f"{table}: {got.get(table)} rows restored, {want.get(table)} backed up")
if unforced:
    problems.append(f"row-level security not forced on: {unforced}")
grants = "not recorded in this backup"
if "app_can_read" in m:
    got_read = json.loads(readable)
    if got_read == m["app_can_read"]:
        grants = "yes"
    else:
        grants = "no"
        problems.append(f"ivaas_app's grants differ: {got_read} restored, {m['app_can_read']} backed up")
log = open(report + ".restore.log").read().strip()
errors = [line for line in log.splitlines() if "error" in line.lower()]
verdict = "PASS" if not problems else "FAIL"
lines = [
    "# Restore drill", "",
    f"**{verdict}**: {dump}, taken {m['taken_at']}, schema {m['alembic_version']}.", "",
    "| Measure | Value | Target (T10.2) |", "|---|---|---|",
    f"| RPO: age of the newest backup | {rpo:.0f} min | 15 min |",
    f"| RTO: restore and verify | {int(total_s)} s (restore {restore_s} s) | 4 h |",
    f"| Tables matching their row counts | {len(want) - sum(1 for p in problems if ':' in p and 'rows' in p)} of {len(want)} | all |",
    f"| Row-level security forced on tenant tables | {'yes' if not unforced else 'no'} | yes |",
    f"| The app role's grants as backed up | {grants} | yes |", "",
]
if problems:
    lines += ["## What did not match", "", *[f"- {p}" for p in problems], ""]
lines += [f"pg_restore exited {rc}" + (f"; {len(errors)} line(s) mention an error, see the .restore.log" if errors else "") + "."]
open(report, "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
sys.exit(0 if verdict == "PASS" else 1)
PY
