#!/usr/bin/env python3
"""A silo installation for one tenant (proposal §2.2, M8 T8.6).

    python deploy/silo/silo.py create <tenant> --slot N   # its env file: ports, secrets
    python deploy/silo/silo.py up <tenant>                # build and start it
    python deploy/silo/silo.py import <tenant> <export>   # fill it from the tenant's export
    python deploy/silo/silo.py accept <tenant>            # T8.6: T1-T7 against it
    python deploy/silo/silo.py down <tenant>              # stop it; its data is kept

A silo is the same application with nothing shared:
- its own compose project (`ivaas-silo-<tenant>`), volumes (`ivaas-silo-<tenant>_*`),
  ports, secrets and backup folder;
- configured to host one tenant (IVAAS_SILO_TENANT), with no demo data or accounts.

The way in is the tenant's own export from the pooled platform (T8.4). Once the silo
is accepted, the pooled copy is cancelled and purged (T8.5).

Standard library only: this runs on the host that runs Docker.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
#: host ports, each a base plus the silo's slot (0-79), clear of the pooled stack's
PORTS = {
    "IVAAS_API_PORT": 18000,
    "IVAAS_PORTAL_PORT": 18100,
    "IVAAS_MINIO_CONSOLE_PORT": 18200,
    "IVAAS_SILO_PG_PORT": 18300,
    "IVAAS_RTSP_PORT": 18400,
    "IVAAS_WHEP_PORT": 18500,
}


def env_path(tenant: str) -> Path:
    return HERE / f"{tenant}.env"


def read_env(tenant: str) -> dict[str, str]:
    path = env_path(tenant)
    if not path.exists():
        sys.exit(f"no silo for {tenant}: run `create {tenant} --slot N` first")
    out = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def compose(tenant: str, *args: str, check: bool = True, capture: bool = False):
    cmd = [
        "docker", "compose",
        "-p", f"ivaas-silo-{tenant}",
        "--env-file", str(env_path(tenant)),
        "-f", str(ROOT / "docker-compose.yml"),
        "-f", str(HERE / "silo.compose.yml"),
        *args,
    ]  # fmt: skip
    return subprocess.run(cmd, cwd=ROOT, check=check, capture_output=capture, text=True)


def create(tenant: str, slot: int) -> None:
    if not SLUG.match(tenant):
        sys.exit("the tenant is its short name: lower case, digits and hyphens")
    if not 0 <= slot < 80:
        sys.exit("--slot is 0-79: it places the silo's ports, so each silo needs its own")
    path = env_path(tenant)
    if path.exists():
        sys.exit(f"{path} exists: a silo's secrets are made once and never regenerated")
    taken = {
        line.split("=", 1)[1]
        for other in HERE.glob("*.env")
        for line in other.read_text().splitlines()
        if line.startswith("IVAAS_API_PORT=")
    }
    if str(PORTS["IVAAS_API_PORT"] + slot) in taken:
        sys.exit(f"slot {slot} is another silo's")
    fernet = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    values = {
        "IVAAS_SILO_TENANT": tenant,
        "IVAAS_VOLUME_PREFIX": f"ivaas-silo-{tenant}",
        **{k: str(base + slot) for k, base in PORTS.items()},
        "IVAAS_BACKUP_DIR": f"./backups/silo-{tenant}",
        "IVAAS_SECRETS_KEY": fernet,
        "IVAAS_AUTH_LOCAL_SECRET": secrets.token_urlsafe(32),
        "IVAAS_PIPELINE_KEY": secrets.token_urlsafe(24),
        "IVAAS_OBJECT_LINK_SECRET": secrets.token_urlsafe(32),
        "IVAAS_CERTIFICATE_SECRET": secrets.token_urlsafe(32),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(24),
    }
    body = "\n".join(f"{k}={v}" for k, v in values.items())
    path.write_text(
        f"# silo for {tenant}: made by deploy/silo/silo.py. Secrets: keep it, never commit it.\n"
        f"{body}\n"
    )
    path.chmod(0o600)
    print(f"{path} written (mode 600). Portal: http://localhost:{values['IVAAS_PORTAL_PORT']}")


def up(tenant: str) -> None:
    read_env(tenant)
    compose(tenant, "up", "-d", "--build")


def down(tenant: str) -> None:
    read_env(tenant)
    compose(tenant, "down")  # never -v: the silo's data stays until it is purged


def import_export(tenant: str, export: Path) -> None:
    read_env(tenant)
    if not export.is_file():
        sys.exit(f"no export at {export}")
    compose(tenant, "cp", str(export), "api:/tmp/tenant-export.zip")
    try:
        result = compose(
            tenant, "exec", "-T", "api",
            "/app/.venv/bin/python", "-m", "ivaas.tools.silo", "import", "/tmp/tenant-export.zip",
            check=False,
        )  # fmt: skip
    finally:
        compose(tenant, "exec", "-T", "api", "rm", "-f", "/tmp/tenant-export.zip", check=False)
    sys.exit(result.returncode)


def psql(tenant: str, sql: str) -> str:
    out = compose(
        tenant, "exec", "-T", "postgres", "psql", "-U", "ivaas", "-d", "postgres", "-tAc", sql,
        capture=True,
    )  # fmt: skip
    return out.stdout.strip()


def accept(tenant: str) -> None:
    """T8.6: the T1-T7 suites against the silo's database server, from a database of
    their own (they reset tables), then the silo itself checked as it runs."""
    env = read_env(tenant)
    db = "ivaas_acceptance"
    psql(tenant, f"DROP DATABASE IF EXISTS {db}")
    psql(tenant, f"CREATE DATABASE {db}")
    url = (
        f"postgresql+asyncpg://ivaas:{env['POSTGRES_PASSWORD']}@127.0.0.1:"
        f"{env['IVAAS_SILO_PG_PORT']}/{db}"
    )
    try:
        suites = subprocess.run(
            ["uv", "run", "pytest", "-q", "-p", "no:warnings"],
            cwd=ROOT / "services" / "api",
            env={**os.environ, "IVAAS_TEST_DATABASE_URL": url},
        )
    finally:
        # what the suites made on the silo's server goes: theirs, and any scratch
        # databases the export and silo tests created
        leftovers = psql(
            tenant,
            "SELECT string_agg(datname, ' ') FROM pg_database "
            "WHERE datname ~ '^(scratch|silo)_[0-9a-f]{8}$'",
        )
        for name in [db, *leftovers.split()]:
            psql(tenant, f"DROP DATABASE IF EXISTS {name}")

    head = sorted((ROOT / "services/api/migrations/versions").glob("0*.py"))[-1].stem
    live = compose(
        tenant, "exec", "-T", "postgres", "psql", "-U", "ivaas", "-d", "ivaas", "-tAc",
        "SELECT version_num FROM alembic_version", capture=True,
    ).stdout.strip()  # fmt: skip
    tenants = compose(
        tenant, "exec", "-T", "postgres", "psql", "-U", "ivaas", "-d", "ivaas", "-tAc",
        "SET app.scope = 'system'; SELECT string_agg(slug, ',') FROM tenants", capture=True,
    ).stdout.strip().splitlines()[-1]  # fmt: skip
    checks = {
        "the T1-T7 suites pass against the silo's database server": suites.returncode == 0,
        f"the silo's schema is the code's ({head})": live == head,
        f"the silo hosts {tenant} and nothing else": tenants in (tenant, ""),
    }
    for what, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {what}")
    if tenants == "":
        print("note: no tenant imported yet; run `import` with its export")
    sys.exit(0 if all(checks.values()) else 1)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("tenant")
    c.add_argument("--slot", type=int, required=True)
    for name in ("up", "down", "accept"):
        sub.add_parser(name).add_argument("tenant")
    i = sub.add_parser("import")
    i.add_argument("tenant")
    i.add_argument("export", type=Path)
    a = p.parse_args()
    if a.cmd == "create":
        create(a.tenant, a.slot)
    elif a.cmd == "import":
        import_export(a.tenant, a.export)
    else:
        {"up": up, "down": down, "accept": accept}[a.cmd](a.tenant)


if __name__ == "__main__":
    main()
