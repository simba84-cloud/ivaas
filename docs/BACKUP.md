# Backups and the restore drill (proposal T10.2)

The POC's evidence lives in this database: every load, every tally sheet, every
correction, and the availability history behind the reliability figure. Losing it
mid-POC would cost the POC. On 2026-10-01 a full disk took the database down for an
afternoon. It came back intact, but it showed how one bad moment is enough.

## What is backed up, and how often

| What | How | How often | Kept |
|---|---|---|---|
| The database | `pg_dump` (custom format) from the `backup` service, with the roles it grants to (`pg_dumpall --roles-only`, no passwords) | every 15 min (`IVAAS_BACKUP_EVERY_MIN`) | the last 96 (a day), and the first of each day for 30 days |
| The object store (evidence clips, uploaded videos, filed reports) | `mc mirror` from the `backup-objects` service | every hour (`IVAAS_BACKUP_OBJECTS_EVERY_MIN`) | a mirror: what the store holds now |

Both write to `IVAAS_BACKUP_DIR` on the host (default `./backups`), outside Docker's
own disk, so a full or lost Docker VM does not take them with it.

**Each database backup has a manifest** (`ivaas-<time>.json`). It records:
- when it was taken;
- the schema version;
- the dump's size and SHA-256;
- the exact row count of every table;
- the tables the API's role may read.

The counts and grants are taken in the same snapshot as the dump, so they describe
exactly what the dump holds, even while heartbeats and counts keep arriving.

`backups/db/LAST_OK` and `backups/objects/LAST_OK` hold the time of the last success.
`LAST_ERROR` appears when a run fails. Watch them. A backup that silently stopped is
worse than none, because it is trusted.

**Copy `IVAAS_BACKUP_DIR` off the machine** (rsync, or object storage in another place).
A backup on the same server is not disaster recovery. On the POC server, point
`IVAAS_BACKUP_DIR` at a separate disk as well.

## The restore drill

```bash
./deploy/backup/restore-drill.sh                 # the newest backup
./deploy/backup/restore-drill.sh backups/db/ivaas-<time>.dump
```

The drill:
1. Checks the dump's SHA-256 against its manifest.
2. Restores it into a throwaway Postgres with no network, so the real one can't be
   touched, using TimescaleDB's pre- and post-restore steps.
3. Requires the restored copy to match the manifest **exactly**: the schema version,
   every table's row count, row-level security forced on every tenant table, and the
   API role's grants as they were.
4. Reports **RPO** (how old the backup is: what a disaster now would lose) and **RTO**
   (how long the restore and checks took).
5. Removes the throwaway server and its data. The report is written to `backups/drills/`.
   It exits 1 if anything does not match.

Run it **weekly, before the POC starts, and after any change to the database or the
backups.** A backup that has never been restored is a hope, not a backup.

### Results, 2026-10-01 (development stack)

- **PASS:** all 32 tables matched their row counts exactly, row-level security was
  forced on every tenant table, and the API role's grants came back as backed up.
- **RPO 0 min** at the time of the drill, and at most 15 min by schedule.
- **RTO 7 s** for the 12 MB database. T10.2's targets are 15 min and 4 h.
- The object mirror held 126 of 126 objects (3.2 GiB), the same as the bucket.

**The drill was made to fail, to show it can:**
- A manifest claiming one extra load failed, naming that one table.
- A dump changed after it was taken was refused before restoring.
- A restore without its roles file failed: the API's role was missing, so the API could
  not have read a row. This is why every backup keeps its roles.

## Restoring for real

Onto a new server, after a loss:
1. Bring up Postgres alone: `docker compose up -d postgres`. Do not start the API yet,
   because it runs migrations at startup.
2. Apply the roles, then restore with TimescaleDB's steps, as the drill does:

   ```bash
   B=backups/db/ivaas-<time>
   docker compose exec -T postgres psql -U ivaas -d ivaas < $B.roles.sql   # "already exists" is fine
   docker compose exec -T postgres psql -U ivaas -d ivaas -c "SELECT timescaledb_pre_restore();"
   docker compose exec -T postgres pg_restore -U ivaas -d ivaas --no-owner --role=ivaas < $B.dump
   docker compose exec -T postgres psql -U ivaas -d ivaas -c "SELECT timescaledb_post_restore();"
   ```
3. Put the objects back: `mc mirror backups/objects/ivaas store/ivaas`, using the MinIO
   image's `mc`, as the `backup-objects` service does.
4. Start the rest: `docker compose up -d`.
5. Check a few sessions, a report and a clip in the portal before anyone relies on it.

## Limits

- **The object mirror has no history.** An object deleted from the store leaves the
  mirror within the hour. The database backups keep 30 days of history. For GA, turn on
  bucket versioning.
- **RPO is the schedule**, 15 minutes. Continuous archiving (WAL shipping, for example
  pgBackRest) would bring it to seconds when that matters.
- **The drill is not in CI.** It needs a real backup of the real database, so run it
  where the backups are.
- **To run one backup or one mirror by hand:** `docker compose run --rm backup once`,
  or `docker compose run --rm backup-objects once`.
