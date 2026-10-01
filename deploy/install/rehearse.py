"""Install day, rehearsed (proposal M9, days 1-2): T2.1 and T2.5 before anyone is on site.

Runs what the installer will do at Bakers Inn, in order, against this stack, with
simulated cameras in place of the real ones, and times every step:

  1. preflight     the platform answers, the models and footage are there, clocks agree
  2. enrol         a token for the bay, the node enrols with it, the token cannot be reused
  3. cameras       the 17 devices are registered and the node is configured for them
  4. start         cameras streaming, the node running
  5. healthy       every camera online, the node reporting every stream connected
  6. unplug        one camera's cable pulled: the platform says so within 60 s, and
                   says it is back when plugged in again
  7. counting      the node counts what the footage shows (a sanity check, not accuracy)

then removes what it made, unless --keep. It writes a report to deploy/install/reports/.

It uses its own node and camera services (rehearsal.compose.yml), so the real node's
credential and container are never touched, and refuses a bay that has real cameras.

    IVAAS_ADMIN_USER=b-admin IVAAS_ADMIN_PASSWORD=... \\
      uv run --with httpx python deploy/install/rehearse.py --bay <bay id>

On a laptop, --cameras 2 --stride 2 --min-fps 0.1 rehearses the steps without asking a
CPU to decode seventeen streams; a reduced array is all chokepoints, so it still counts.
The full array, in the scope's layout, is for the edge node itself.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "docker-compose.yml"),
    "-f",
    str(ROOT / "deploy/install/rehearsal.compose.yml"),
    "--profile",
    "rehearsal",
]
STREAMS = "/sim/rehearsal-streams.txt"
SIM_PREFIX = "Sim "
#: the counter is proven at this many frames a second (the golden-footage test reads
#: every frame of 10 fps footage); a chokepoint processed much slower misses crossings
COUNTING_FPS = 10


@dataclass
class Step:
    name: str
    result: str = "skipped"  # pass | warn | fail | skipped
    seconds: float | None = None
    notes: list[str] = field(default_factory=list)

    def note(self, line: str) -> None:
        self.notes.append(line)
        print(f"    {line}", flush=True)


class Failed(Exception):
    pass


class Rehearsal:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.steps: list[Step] = []
        self.api = httpx.Client(base_url=args.api.rstrip("/"), timeout=20.0)
        self.site_id = self.node_id = self.token = None
        self.cameras: list[dict] = []
        self.chokepoint_fps: list[float] = []

    # --- plumbing ---------------------------------------------------------------
    def step(self, name: str):
        rehearsal = self

        class _Timed:
            def __enter__(self):
                self.s = Step(name)
                rehearsal.steps.append(self.s)
                self.t0 = time.monotonic()
                print(f"\n== {name}", flush=True)
                return self.s

            def __exit__(self, kind, exc, tb):
                self.s.seconds = round(time.monotonic() - self.t0, 1)
                if exc is not None:
                    self.s.result = "fail"
                    self.s.note(f"FAILED: {exc}")
                elif self.s.result == "skipped":
                    self.s.result = "pass"
                print(f"   -> {self.s.result} ({self.s.seconds} s)", flush=True)
                return False

        return _Timed()

    def get(self, path: str, **kw):
        r = self.api.get(path, **kw)
        if r.status_code >= 400:
            raise Failed(f"GET {path}: {r.status_code} {r.text[:200]}")
        return r.json()

    def post(self, path: str, **kw):
        r = self.api.post(path, **kw)
        if r.status_code >= 400:
            raise Failed(f"POST {path}: {r.status_code} {r.text[:200]}")
        return r.json() if r.content else {}

    def compose(self, *args: str, env: dict | None = None, check: bool = True) -> str:
        r = subprocess.run(
            [*COMPOSE, *args],
            cwd=ROOT,
            capture_output=True,
            check=False,  # the exit code is looked at below
            text=True,
            env={**os.environ, **(env or {})},
        )
        if check and r.returncode != 0:
            raise Failed(f"docker compose {' '.join(args[:3])}: {r.stderr.strip()[-400:]}")
        return r.stdout

    def wait(self, what: str, check, timeout: float, every: float = 2.0):
        start = time.monotonic()
        while True:
            got = check()
            if got:
                return got, time.monotonic() - start
            if time.monotonic() - start > timeout:
                raise Failed(f"{what}: not after {int(timeout)} s")
            time.sleep(every)

    # --- the day ----------------------------------------------------------------
    def preflight(self) -> None:
        with self.step("1. Preflight") as s:
            r = self.api.get("/auth/config")
            if r.status_code != 200:
                raise Failed(f"the platform does not answer at {self.args.api}")
            server = parsedate_to_datetime(r.headers["date"])
            drift = abs((datetime.now(UTC) - server).total_seconds())
            s.note(f"platform answers; clock difference with this machine {drift:.0f} s")
            if drift > 5:
                s.result = "warn"
                s.note("set the edge node's clock by NTP: tally sheets and clips match by time")
            user, password = (
                os.environ.get("IVAAS_ADMIN_USER"),
                os.environ.get("IVAAS_ADMIN_PASSWORD"),
            )
            if not user or not password:
                raise Failed("set IVAAS_ADMIN_USER and IVAAS_ADMIN_PASSWORD (a tenant admin)")
            r = self.api.post("/auth/login", json={"username": user, "password": password})
            if r.status_code != 200:
                raise Failed(f"sign-in as {user} failed: {r.status_code}")
            self.api.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
            bays = {b["id"]: b for b in self.get("/bays")}
            bay = bays.get(self.args.bay)
            if bay is None:
                raise Failed(f"bay {self.args.bay} is not one {user} can see")
            self.site_id = bay["site_id"]
            real = [
                c["name"]
                for c in self.get(f"/bays/{self.args.bay}/cameras")
                if not c["name"].startswith(SIM_PREFIX)
            ]
            if real:
                raise Failed(
                    f"bay {bay['name']} has real cameras ({len(real)}); rehearse elsewhere"
                )
            s.note(f"bay {bay['name']}: no real cameras, safe to rehearse in")
            for needed in (
                ROOT / "models/stacks-v2.onnx",
                ROOT / "models/layers-v3.onnx",
                ROOT / "deploy/simulator/media/sim.mp4",
            ):
                if not needed.exists():
                    raise Failed(
                        f"missing {needed.relative_to(ROOT)} (see deploy/simulator/README.md)"
                    )
            s.note("models and footage present")

    def enrol(self) -> None:
        with self.step("2. Enrol the node (T2.1)") as s:
            name = f"Rehearsal {datetime.now().astimezone():%Y-%m-%d %H:%M}"
            made = self.post(
                f"/sites/{self.site_id}/enrollment-tokens",
                json={"name": name, "bay_id": self.args.bay, "ttl_hours": 2},
            )
            self.token = made["token"]
            s.note(f"token for {name}, valid until {made['expires_at']}")
            self.compose(
                "run",
                "--rm",
                "--no-deps",
                "rehearsal-node",
                "python",
                "-m",
                "ivaas_pipeline",
                "enroll",
                "--api",
                "http://api:8000",
                "--token",
                self.token,
            )
            node = next((n for n in self.get("/edge/nodes") if n["name"] == name), None)
            if node is None:
                raise Failed("the node enrolled but is not listed")
            self.node_id = node["id"]
            s.note(f"enrolled: node {node['id'][:8]} at this site, health '{node['health']}'")
            again = self.api.post("/edge/enroll", json={"token": self.token})
            if again.status_code != 401:
                raise Failed(f"the token worked twice ({again.status_code})")
            s.note("the token is spent: a second use is refused")

    def register(self) -> None:
        with self.step(f"3. Register {self.args.cameras + 1} devices") as s:
            self.compose(
                "run",
                "--rm",
                "--no-deps",
                "-e",
                "IVAAS_ADMIN_USER",
                "-e",
                "IVAAS_ADMIN_PASSWORD",
                "rehearsal-node",
                "python",
                "-m",
                "ivaas_pipeline.sim",
                "setup",
                "--api",
                "http://api:8000",
                "--bay",
                self.args.bay,
                "--node",
                self.node_id,
                "--cameras",
                str(self.args.cameras),
                "--lpr",
                "1",
                "--stride",
                str(self.args.stride),
                "--out",
                STREAMS,
                # the scope's layout lists chokepoints last; cut short it would have
                # none, and nothing would count. A reduced rig is all chokepoints.
                *([] if self.args.cameras >= 16 else ["--role", "chokepoint"]),
            )
            self.cameras = [
                c
                for c in self.get(f"/bays/{self.args.bay}/cameras")
                if c["name"].startswith(SIM_PREFIX)
            ]
            roles = sorted({c["role"] for c in self.cameras})
            s.note(f"{len(self.cameras)} cameras registered ({', '.join(roles)})")
            if len(self.cameras) != self.args.cameras + 1:
                raise Failed(f"expected {self.args.cameras + 1}, found {len(self.cameras)}")

    def start(self) -> None:
        with self.step("4. Start the cameras and the node") as s:
            self.compose("up", "-d", "--no-deps", "rehearsal-cameras", "rehearsal-node")
            s.note("rehearsal-cameras and rehearsal-node are running")

    def node(self) -> dict:
        return next(n for n in self.get("/edge/nodes") if n["id"] == self.node_id)

    def healthy(self) -> None:
        with self.step("5. All streams healthy (T2.5)") as s:
            ids = {c["id"] for c in self.cameras}

            def platform_sees_all():
                cams = self.get(f"/bays/{self.args.bay}/cameras")
                up = {c["id"] for c in cams if c["status"] == "online"}
                return ids <= up

            _, took = self.wait("cameras online", platform_sees_all, self.args.timeout)
            s.note(f"platform: all {len(ids)} cameras online after {took:.0f} s")

            # connected is not enough: a stream the node cannot keep up with is connected
            # and counts nothing. Healthy means frames are being processed from every one.
            def working(n):
                return {
                    c["api_camera_id"]
                    for c in n["cameras"]
                    if c["connected"] and (c["fps"] or 0) >= self.args.min_fps
                }

            def node_reports_all():
                n = self.node()
                applied = n["applied_config_version"] == n["config_version"]
                return n if n["health"] == "online" and applied and ids <= working(n) else None

            try:
                n, took2 = self.wait("every stream working", node_reports_all, self.args.timeout)
            except Failed:
                n = self.node()
                names = {c["id"]: c["name"] for c in self.cameras}
                short = sorted(names[i] for i in ids - working(n))
                raise Failed(
                    f"{len(short)} of {len(ids)} streams below {self.args.min_fps} fps after "
                    f"{int(self.args.timeout)} s (node health '{n['health']}'): "
                    f"{', '.join(short[:6])}{' ...' if len(short) > 6 else ''}. The node is "
                    "short of decode for this many streams, or they are not arriving"
                ) from None
            fps = sorted(c["fps"] or 0 for c in n["cameras"])
            chokepoints = {c["id"] for c in self.cameras if c["role"] == "chokepoint"}
            self.chokepoint_fps = [
                c["fps"] or 0 for c in n["cameras"] if c["api_camera_id"] in chokepoints
            ]
            s.note(
                f"node: online, configuration applied, {len(n['cameras'])} streams connected "
                f"after {took + took2:.0f} s; fps {fps[0]:.1f}-{fps[-1]:.1f}; "
                f"spool {n['spool_pending']} pending"
            )
            slow = [c for c in n["cameras"] if (c["lag_s"] or 0) > 2]
            if slow:
                s.result = "warn"
                s.note(f"{len(slow)} stream(s) more than 2 s behind: the node is short of decode")

    def unplug(self) -> None:
        with self.step("6. Unplug a camera (T2.5)") as s:
            cam = next(c for c in self.cameras if c["role"] != "lpr")
            path, flag = cam["stream_path"], cam["stream_path"].replace("/", "_")

            def status():
                return next(
                    c["status"]
                    for c in self.get(f"/bays/{self.args.bay}/cameras")
                    if c["id"] == cam["id"]
                )

            self.compose(
                "exec",
                "-T",
                "rehearsal-cameras",
                "sh",
                "-c",
                f"touch /tmp/unplugged/{flag}; pkill -f 'rtsp://.*/{path}$' || true",
            )
            s.note(f"pulled the cable on {cam['name']}")
            _, took = self.wait("camera reported offline", lambda: status() == "offline", 180, 1)
            s.note(f"platform reports it offline after {took:.0f} s (target: under 60 s)")
            if took > 60:
                raise Failed(f"took {took:.0f} s")
            self.compose("exec", "-T", "rehearsal-cameras", "rm", "-f", f"/tmp/unplugged/{flag}")
            _, back = self.wait("camera back online", lambda: status() == "online", 180, 1)
            s.note(f"plugged back in: online again after {back:.0f} s")

    def counting(self) -> None:
        with self.step("7. Counting sanity check") as s:

            def counted():
                rows = self.get("/sessions", params={"bay_id": self.args.bay, "limit": 20})
                return [r for r in rows if r["ai_count"] > 0 and r["opened_at"] >= self.began]

            try:
                rows, took = self.wait("a load counted", counted, self.args.count_timeout, 5)
            except Failed:
                s.result = "warn"
                slowest = min(self.chokepoint_fps, default=0)
                if slowest < COUNTING_FPS / 2:
                    s.note(
                        f"nothing counted in {self.args.count_timeout} s: inconclusive. The "
                        f"chokepoints were processed at {slowest:.1f} fps and the counter is "
                        f"proven at {COUNTING_FPS}: this machine cannot rehearse counting live."
                    )
                else:
                    s.note(
                        f"nothing counted in {self.args.count_timeout} s at "
                        f"{slowest:.1f} fps: look at the live view. Is the chokepoint line "
                        "where the stacks cross, and is the stride 1?"
                    )
                return
            s.note(
                f"{len(rows)} load(s) counted after {took:.0f} s, "
                f"{sum(r['ai_count'] for r in rows)} crates (footage, not truth)"
            )

    def clean_up(self) -> None:
        with self.step("Clean up") as s:
            if self.args.keep:
                s.result = "skipped"
                s.note("--keep: the node, cameras and containers are left for a look")
                return
            self.compose("rm", "-sf", "rehearsal-node", "rehearsal-cameras", check=False)
            if self.node_id:
                self.api.delete(f"/edge/nodes/{self.node_id}")
                s.note("node revoked")
            if self.cameras or self.node_id:
                self.compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "-e",
                    "IVAAS_ADMIN_USER",
                    "-e",
                    "IVAAS_ADMIN_PASSWORD",
                    "rehearsal-node",
                    "python",
                    "-m",
                    "ivaas_pipeline.sim",
                    "teardown",
                    "--api",
                    "http://api:8000",
                    "--bay",
                    self.args.bay,
                    check=False,
                )
                s.note("simulated cameras removed")
            s.note("loads it counted stay in the bay's history, as any counted load does")

    def run(self) -> int:
        self.began = datetime.now(UTC).isoformat()
        try:
            for part in (
                self.preflight,
                self.enrol,
                self.register,
                self.start,
                self.healthy,
                self.unplug,
                self.counting,
            ):
                part()
        except Failed:
            pass  # recorded on its step; the rest is not attempted
        finally:
            try:
                self.clean_up()
            except Exception as exc:  # noqa: BLE001 - the report still gets written
                print(f"clean-up failed: {exc}")
        return self.report()

    def report(self) -> int:
        failed = any(s.result == "fail" for s in self.steps)
        warned = any(s.result == "warn" for s in self.steps)
        verdict = "FAIL" if failed else "PASS, with warnings" if warned else "PASS"
        out = ROOT / "deploy/install/reports"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"rehearsal-{datetime.now().astimezone():%Y%m%d-%H%M}.md"
        total = sum(s.seconds or 0 for s in self.steps)
        lines = [
            "# Install-day rehearsal",
            "",
            (
                f"**{verdict}** in {total / 60:.1f} min. {self.args.cameras} volumetric + 1 LPR "
                f"simulated cameras, stride {self.args.stride}, at least {self.args.min_fps} fps "
                f"per stream, against {self.args.api}."
            ),
            "",
            "| Step | Result | Time (s) |",
            "|---|---|---|",
            *[f"| {s.name} | {s.result} | {s.seconds} |" for s in self.steps],
            "",
        ]
        for s in self.steps:
            if s.notes:
                lines += [f"## {s.name}", "", *[f"- {n}" for n in s.notes], ""]
        path.write_text("\n".join(lines))
        print(f"\n{verdict}. Report: {path.relative_to(ROOT)}")
        return 1 if failed else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--bay", required=True, help="a bay with no real cameras")
    port = os.environ.get("IVAAS_PORTAL_PORT", "8080")
    p.add_argument("--api", default=f"http://localhost:{port}/api/v1")
    p.add_argument("--cameras", type=int, default=16, help="volumetric cameras (scope: 16)")
    p.add_argument("--stride", type=int, default=1, help="the node reads every n-th frame")
    p.add_argument("--timeout", type=float, default=600, help="seconds to reach healthy")
    p.add_argument(
        "--min-fps",
        type=float,
        default=2.0,
        help="frames a second each stream must be processed at to count as working",
    )
    p.add_argument("--count-timeout", type=float, default=300)
    p.add_argument("--keep", action="store_true", help="leave everything running afterwards")
    return Rehearsal(p.parse_args()).run()


if __name__ == "__main__":
    sys.exit(main())
