#!/usr/bin/env bash
# The database, every IVAAS_BACKUP_EVERY_MIN minutes (proposal T10.2: RPO 15 min).
# Runs in the `backup` container, which uses the database's own image so pg_dump
# always matches the server.
#
# Each backup is a pg_dump (custom format), the roles it grants to, and a manifest: when it was taken, the
# schema version, the dump's size and SHA-256, and the exact row count of every table.
# The counts are taken in the same snapshot as the dump (pg_export_snapshot), so the
# restore drill can hold a restored copy to them exactly, even while heartbeats and
# counts keep arriving.
#
# Kept: the last IVAAS_BACKUP_KEEP backups, and the first of each day for
# IVAAS_BACKUP_DAILY_DAYS days. A backup on the same machine is not a disaster
# recovery copy: copy /backups off it (see docs/BACKUP.md).
set -uo pipefail
OUT="${BACKUP_DIR:-/backups/db}"
EVERY="${IVAAS_BACKUP_EVERY_MIN:-15}"
KEEP="${IVAAS_BACKUP_KEEP:-96}"
DAILY_DAYS="${IVAAS_BACKUP_DAILY_DAYS:-30}"
mkdir -p "$OUT"

COUNTS="SELECT json_object_agg(table_name, (xpath('/row/c/text()', query_to_xml(
  format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text::bigint
  ORDER BY table_name) FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"

# what the API's role may read, so a restore can be held to its grants too
READABLE="SELECT coalesce(json_agg(table_name ORDER BY table_name), '[]') FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  AND has_table_privilege('ivaas_app', format('public.%I', table_name), 'SELECT');"

log() { echo "$(date -u +%FT%TZ) $*"; }

backup_once() {
  local stamp base snap counts readable version bytes sha
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  base="$OUT/ivaas-$stamp"
  # one transaction, held open: the dump and the counts see the same instant
  coproc PSQL { psql -X -q -At -v ON_ERROR_STOP=1 2>&1; }
  echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();" >&"${PSQL[1]}"
  read -r -t 30 snap <&"${PSQL[0]}" || { log "no snapshot from the database"; return 1; }
  if ! pg_dump -Fc --snapshot="$snap" -f "$base.dump.part" 2>"$base.err"; then
    log "pg_dump failed: $(tail -3 "$base.err")"
    echo "ROLLBACK;" >&"${PSQL[1]}"
    rm -f "$base.dump.part" "$base.err"
    return 1
  fi
  echo "$COUNTS" >&"${PSQL[1]}"
  read -r -t 60 counts <&"${PSQL[0]}"
  echo "$READABLE" >&"${PSQL[1]}"
  read -r -t 30 readable <&"${PSQL[0]}"
  echo "SELECT version_num FROM alembic_version;" >&"${PSQL[1]}"
  read -r -t 30 version <&"${PSQL[0]}"
  echo "COMMIT;" >&"${PSQL[1]}"
  eval "exec ${PSQL[1]}>&-"
  wait "$PSQL_PID" 2>/dev/null
  rm -f "$base.err"
  # roles are not in a pg_dump; the schema grants to ivaas_app, so keep them (no passwords)
  pg_dumpall --roles-only --no-role-passwords >"$base.roles.sql" 2>/dev/null || true
  mv "$base.dump.part" "$base.dump"
  bytes=$(stat -c %s "$base.dump")
  sha=$(sha256sum "$base.dump" | cut -d' ' -f1)
  cat >"$base.json" <<JSON
{"taken_at": "$(date -u +%FT%TZ)", "file": "$(basename "$base.dump")", "bytes": $bytes,
 "sha256": "$sha", "alembic_version": "$version", "pg_dump": "$(pg_dump --version | cut -d' ' -f3)",
 "app_can_read": $readable, "rows": $counts}
JSON
  date -u +%FT%TZ >"$OUT/LAST_OK"
  log "backed up to $(basename "$base.dump"): $bytes bytes, schema $version"
}

prune() {
  # newest first; keep the last $KEEP, and the first of each day for $DAILY_DAYS days
  local all=() keep=() day seen="" cutoff
  mapfile -t all < <(ls -1 "$OUT"/ivaas-*.dump 2>/dev/null | sort -r)
  # busybox date: no "-30 days", but it does take @epoch
  cutoff=$(date -u -d "@$(($(date +%s) - DAILY_DAYS * 86400))" +%Y%m%d)
  for i in "${!all[@]}"; do
    f="${all[$i]}"
    day=$(basename "$f" | cut -c7-14)
    if [ "$i" -lt "$KEEP" ]; then keep+=("$f"); continue; fi
    if [ "$day" -ge "$cutoff" ] && [[ " $seen " != *" $day "* ]]; then
      seen="$seen $day"
      keep+=("$(ls -1 "$OUT"/ivaas-"$day"T*.dump | sort | head -1)")
    fi
  done
  for f in "${all[@]}"; do
    if [[ " ${keep[*]} " != *" $f "* ]]; then rm -f "$f" "${f%.dump}.json" "${f%.dump}.roles.sql"; fi
  done
}

if [ "${1:-}" = "prune" ]; then
  prune
  exit $?
fi
if [ "${1:-}" = "once" ]; then
  backup_once && prune
  exit $?
fi
log "backing up every $EVERY min to $OUT; keeping $KEEP, and one a day for $DAILY_DAYS days"
while true; do
  if backup_once; then prune; else date -u +%FT%TZ >"$OUT/LAST_ERROR"; fi
  sleep $((EVERY * 60))
done
