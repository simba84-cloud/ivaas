# Silo installations (M8, T8.6)

A **silo** is one tenant on an installation of its own: database, object store, broker,
API, worker and portal, sharing nothing with the pooled platform. Proposal §2.2 offers
it to enterprise or regulated customers. It runs the same code, configured to host one
tenant (`IVAAS_SILO_TENANT`), with no demo data and no demo accounts.

Everything below is `deploy/silo/silo.py`, run on the host that runs Docker.

## Moving a tenant from the pooled platform into a silo

1. **Export.** The tenant's owner downloads its export: Account → Download the export
   (T8.4). Only the owner can; Cassava never exports a tenant's data.
2. **Create.** `python deploy/silo/silo.py create <tenant> --slot N` writes
   `deploy/silo/<tenant>.env` (mode 600, kept out of git). It holds:
   - the compose project `ivaas-silo-<tenant>` and volumes `ivaas-silo-<tenant>_*`;
   - ports `18000+N`, `18100+N` and so on;
   - its own backup folder;
   - freshly generated keys: secrets, sign-in, pipeline, object links, deletion
     certificates, Postgres and MinIO.

   Keep the file: its keys open the silo's data, and they are never generated twice.
   `--slot` keeps silos on one host apart.
3. **Up.** `python deploy/silo/silo.py up <tenant>`.
4. **Import.** `python deploy/silo/silo.py import <tenant> <export.zip>`:
   - **Checks first:** the export must be this silo's tenant. Any other tenant in the
     database must be an empty stub (migration 0012 makes one for Bakers Inn), which
     is removed. Another tenant with data stops it.
   - **Loads everything:** every row and object, checked against the manifest. All or
     nothing.
   - **Leaves secrets behind,** each one reported. Secrets do not travel: the export
     withholds them, and anything sealed was sealed under the pooled key, which the
     silo does not have.
     - **Accounts** are closed until reset. Each owner gets a temporary password,
       printed once. People with SSO sign in once the client secret is entered again.
     - **Webhooks** get new random secrets. Receivers reject deliveries until the
       tenant re-creates each webhook and shares the new secret, so nothing is
       forgeable in between.
     - **Face enrolments** are removed. Biometric templates do not move; people enrol
       again, with consent.
     - **Camera addresses** are cleared (they carry camera passwords). Each is entered
       again.
     - **The SSO client secret** must be entered again.
     - **Edge nodes** are revoked. Their credentials stayed behind and they report to
       the pooled platform, so each is enrolled again into the silo.
   - **Scans for leftovers:** no withheld marker may remain anywhere. A marker where a
     secret belongs would be a secret everyone knows.
5. **Accept (T8.6).** `python deploy/silo/silo.py accept <tenant>`:
   - **The suites:** it runs the whole T1–T7 test suite against the silo's database
     server, in a database of its own that is dropped afterwards. The tenant's database
     is never touched. Any scratch databases the export and silo tests create are
     dropped too.
   - **The schema:** the silo's must be the code's.
   - **One tenant:** the silo must host its tenant and nothing else.

   It exits non-zero on any failure.
6. **Retire the pooled copy.** Once the silo is accepted and in use, cancel the tenant
   on the pooled platform, and purge it when the retention window has passed. The
   deletion certificate (T8.5) is the record that it left.

`down <tenant>` stops a silo and keeps its data.

## What a silo does not have

**Not started:** Keycloak, Ollama, Grafana and Prometheus are the pooled platform's.
- A silo signs in locally, or through the tenant's own provider (SSO).
- Its assistant is off until it is given a model.
- `docker compose --profile silo-full` starts all four for a silo sized for them.

**WebRTC on a shared host:** a silo sharing a host with the pooled stack leaves
WebRTC's ICE port to it. Live video then uses RTSP or WHEP only. A silo on its own host
drops that override.

**Its own Keycloak:** a silo that should sign in through Keycloak rather than the
tenant's provider needs its own realm.

## Found while building it

All of these are covered by tests now.

- **Undecryptable SSO secret locked the tenant out:** an export from older code
  carried the SSO client secret as ciphertext. The silo could not open it, and every
  password sign-in of the tenant failed, because password sign-in reads the SSO
  settings. Sealed values are now replaced whatever form they arrive in, and an
  unopenable SSO secret reads as "not configured", never as an error.
- **Bakers Inn kept coming back:** the API created Bakers Inn at every start, so a
  silo for anyone else had it back after a restart. Its background metering then gave
  that stub usage rows. A silo now starts with no tenants.
- **Withheld hashes:** a marker in a hash column (password, node credential,
  enrollment token) matches no secret, so it opens nothing. Passwords are reset
  anyway, and nodes are revoked.
