"""Fill a silo installation with its tenant (proposal §2.2, M8 T8.6).

    python -m ivaas.tools.silo import /path/to/<tenant>-export-<date>.zip

Run inside the silo's API container, so it uses that installation's database and
object store (IVAAS_* settings). The export is the tenant's own, from the pooled
platform (T8.4); afterwards the pooled copy is cancelled and purged (T8.5).

1. The export must be the tenant this silo is for (IVAAS_SILO_TENANT).
2. Any other tenant the database holds must be an empty stub (migration 0012 makes
   one for Bakers Inn in every database); stubs are removed. Another tenant with data
   stops the import: a silo is one customer's.
3. Every row and object is loaded, and the counts checked against the manifest: all
   or nothing, as T8.4 requires.
4. Secrets never travel: the export withheld them, and a sealed column cannot be
   opened with this installation's key anyway. So, with every one reported:
   - every account is closed until reset, and each owner gets a temporary password,
     shown once, to reopen the rest (or the tenant's SSO lets people straight in);
   - each webhook gets a new random secret, so a receiver rejects deliveries until the
     tenant re-creates it and shares the new one, rather than accepting forgeries;
   - face enrolments are removed: biometric templates do not move, people re-enrol;
   - the SSO client secret is to be entered again; cameras need their addresses again;
   - edge nodes are revoked: their credentials stayed behind, and they report to the
     pooled platform; each is enrolled again into the silo.
   Then every withheld column is scanned: no marker may be left anywhere.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import zipfile
from pathlib import Path
from typing import Any

from ivaas.adapters.persistence.secrets import SecretBox
from ivaas.adapters.persistence.tenant_data_postgres import WITHHELD_MARK
from ivaas.application.users import generate_temporary_password
from ivaas.config.container import build_container
from ivaas.config.settings import Settings
from ivaas.domain.rbac import Role
from ivaas.tenancy import system_context, tenant_context


class SiloError(Exception):
    pass


#: columns holding a one-way hash: compared as digests, so the marker opens nothing
HASHES = frozenset({"password_hash", "credential_hash", "token_hash"})


async def import_into_silo(container: Any, path: Path, silo: str, box: SecretBox) -> dict[str, Any]:
    if container.tenant_data is None:
        raise SiloError("a silo runs on Postgres: IVAAS_STORAGE=postgres")
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("manifest.json"))
    slug = manifest["tenant"]["slug"]
    if slug != silo:
        raise SiloError(f"this silo is for {silo!r}; the export is {slug!r}")

    removed = []
    with system_context():
        others = [t for t in await container.tenants.list_all() if t.slug != silo]
    for t in others:
        rows = await container.tenant_data.counts(t.id)
        if any(n for table, n in rows.items() if table != "tenants"):
            raise SiloError(f"this database holds {t.slug}'s data: a silo is one customer's")
        await container.tenant_data.purge(t.id)
        removed.append(t.slug)

    result = await container.lifecycle().import_into(path)
    expected = {t: n for t, n in manifest["counts"].items() if t != "partners"}
    if result["counts"] != expected:
        diff = {
            t: (expected.get(t), result["counts"].get(t))
            for t in set(expected) | set(result["counts"])
            if expected.get(t) != result["counts"].get(t)
        }
        raise SiloError(f"the import does not match the export: {diff}")

    tenant_id = result["manifest"]["tenant"]["id"]
    data, t, mark = container.tenant_data, tenant_id, WITHHELD_MARK
    # Every sealed value is replaced, not only the markers: whatever the export held,
    # it was sealed with another installation's key and cannot be opened here (found
    # live: an export from older code carried the SSO secret as foreign ciphertext, and
    # every login of the tenant failed on it).
    hooks = await data.execute("SELECT id FROM webhook_endpoints WHERE tenant_id = :t", {"t": t})
    for (hook_id,) in hooks:
        await data.execute(
            "UPDATE webhook_endpoints SET secret = :s WHERE id = :id",
            {"s": box.seal(secrets.token_urlsafe(32)), "id": hook_id},
        )
    (people,) = await data.execute("DELETE FROM enrolled_people WHERE tenant_id = :t", {"t": t})
    (sso,) = await data.execute(
        "UPDATE sso_configs SET client_secret = :s WHERE tenant_id = :t",
        {"s": box.seal(""), "t": t},
    )
    (addresses_cleared,) = await data.execute(
        "UPDATE cameras SET source_url = NULL WHERE tenant_id = :t AND source_url IS NOT NULL",
        {"t": t},
    )
    (nodes,) = await data.execute(
        "UPDATE edge_nodes SET status = 'revoked', revoked_at = :now "
        "WHERE tenant_id = :t AND revoked_at IS NULL",
        {"t": t, "now": container.clock.now()},
    )
    ((addressless,),) = await data.execute(
        "SELECT count(*) FROM cameras WHERE tenant_id = :t AND source_url IS NULL", {"t": t}
    )

    owners: dict[str, str] = {}
    closed = 0
    now = container.clock.now()
    with system_context():
        users = [u for u in await container.users.list_all() if str(u.tenant_id) == tenant_id]
    for u in users:
        if Role.TENANT_OWNER in u.roles:
            temporary = generate_temporary_password()
            u.password_hash = container.hasher.hash(temporary)
            owners[u.username] = temporary
        else:  # opened again by an owner's reset, or straight away through SSO
            u.password_hash = container.hasher.hash(secrets.token_urlsafe(32))
            closed += 1
        u.must_change_password, u.password_changed_at = True, now
        with tenant_context(u.tenant_id):
            await container.users.save(u)
    # nothing withheld may be left as its marker: a marker read back as a value is a
    # secret everyone knows (the box returns text it did not seal as it is)
    left = []
    for table, columns in result["manifest"]["withheld"].items():
        for column in columns:
            if not column.isidentifier():
                continue  # a description, not a column (secret settings: left out whole)
            if column in HASHES:
                continue  # a marker where a hash belongs matches no secret: nothing opens
            ((n,),) = await data.execute(
                f'SELECT count(*) FROM "{table}" WHERE tenant_id = :t AND "{column}"::text = :m',
                {"t": t, "m": mark},
            )
            if n:
                left.append(f"{table}.{column} ({n})")
    if left:
        raise SiloError(f"withheld markers remain after the import: {', '.join(left)}")
    return {
        "tenant": slug,
        "rows": sum(expected.values()),
        "tables": len(expected),
        "objects": result["objects"],
        "stubs_removed": removed,
        "accounts_closed_until_reset": closed,
        "webhooks_given_new_secrets": len(hooks),
        "face_enrolments_removed": people,
        "sso_client_secret_to_enter_again": bool(sso),
        "cameras_needing_their_address": addressless,
        "camera_addresses_cleared": addresses_cleared,
        "edge_nodes_to_enrol_again": nodes,
        "owner_temporary_passwords": owners,
    }


async def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "import":
        print(__doc__, file=sys.stderr)
        return 2
    settings = Settings()
    if not settings.silo_tenant:
        print("IVAAS_SILO_TENANT is not set: this is not a silo installation", file=sys.stderr)
        return 2
    container = await build_container(settings)
    try:
        box = SecretBox(settings.secrets_keys)
        report = await import_into_silo(container, Path(argv[1]), settings.silo_tenant, box)
    except SiloError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    finally:
        await container.aclose()
    print(json.dumps(report, indent=2))
    print("Owner temporary passwords are shown once, above. Give them to the owners now.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
