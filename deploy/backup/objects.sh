#!/bin/sh
# The object store, mirrored every IVAAS_BACKUP_OBJECTS_EVERY_MIN minutes: evidence
# clips, uploaded videos and filed reports. Clips and uploads cannot be rebuilt from
# the database; reports can. Runs in the `backup-objects` container, which uses the
# MinIO server's own image for its `mc` client.
#
# A mirror, not a history: an object removed from the store (a clip past its
# retention) leaves the mirror at the next run. The database backups keep history.
set -u
EVERY="${IVAAS_BACKUP_OBJECTS_EVERY_MIN:-60}"
OUT="${BACKUP_DIR:-/backups}/objects"
mkdir -p "$OUT"
echo "mirroring store/${IVAAS_S3_BUCKET:-ivaas} to $OUT every $EVERY min"
while true; do
  if mc mirror --overwrite --remove --quiet "store/${IVAAS_S3_BUCKET:-ivaas}" "$OUT/${IVAAS_S3_BUCKET:-ivaas}"; then
    date -u +%FT%TZ >"$OUT/LAST_OK"
    echo "$(date -u +%FT%TZ) mirrored"
  else
    date -u +%FT%TZ >"$OUT/LAST_ERROR"
    echo "$(date -u +%FT%TZ) mirror failed"
  fi
  [ "${1:-}" = "once" ] && exit 0
  sleep $((EVERY * 60))
done
