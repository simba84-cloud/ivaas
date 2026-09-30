"""T2.3's report: can this edge node sustain every stream at the target analytics rate?

    python -m ivaas_pipeline.loadreport --minutes 60 --target-fps 10 --out /sim/report.md

Reads the pipeline's own Prometheus metrics (http://localhost:9102/metrics inside the
pipeline container) at an interval, and at the end states per camera, and overall,
whether the run passed:

- every stream stayed open, with no reconnects (T2.3: "no dropped streams");
- each camera's processed frame rate held at the target (after stride) or above;
- lag stayed bounded, and the delivery spool did not grow without limit.

GPU utilisation and memory are added when `nvidia-smi` is on the node. Numbers are
what was measured; a camera that produced no metrics fails rather than being skipped.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

import httpx
from prometheus_client.parser import text_string_to_metric_families


@dataclass
class Sample:
    at: float
    #: metric name -> {camera (or "" for unlabelled): value}
    values: dict[str, dict[str, float]]
    gpu: dict[str, float] = field(default_factory=dict)


def parse(text: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            if s.name.startswith(("ivaas_pipeline_", "ivaas_delivery_")):
                out[s.name][s.labels.get("camera", s.labels.get("path", ""))] = s.value
    return dict(out)


def gpu_now() -> dict[str, float]:
    """Utilisation %, memory used MiB, and decoder % of GPU 0, if there is one."""
    if not shutil.which("nvidia-smi"):
        return {}
    try:
        line = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,utilization.decoder",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.splitlines()[0]
        util, used, total, dec = (float(x) for x in line.split(","))
        return {"gpu_util": util, "mem_used_mib": used, "mem_total_mib": total, "nvdec": dec}
    except (subprocess.SubprocessError, ValueError, IndexError, OSError):
        return {}


def delivery_counters(text: str) -> dict[str, float]:
    """queued, delivered, dropped (every reason) and pending, summed over event kinds."""
    values = parse(text)
    return {
        name: sum(values.get(f"ivaas_delivery_{name}{suffix}", {}).values())
        for name, suffix in (
            ("queued", "_total"),
            ("delivered", "_total"),
            ("dropped", "_total"),
            ("pending", ""),
        )
    }


@dataclass
class CameraResult:
    camera: str
    fps: float
    lag_max_s: float | None
    dropped_frames: float
    reconnects: float
    always_up: bool
    passed: bool
    why: list[str]


def evaluate(samples: list[Sample], target_fps: float, max_lag_s: float) -> list[CameraResult]:
    first, last = samples[0], samples[-1]
    span = max(last.at - first.at, 1e-9)
    processed = "ivaas_pipeline_frames_processed_total"
    cameras = sorted(
        set(last.values.get(processed, {})) | set(last.values.get("ivaas_pipeline_stream_up", {}))
    )
    results = []
    for cam in cameras:

        def delta(name: str, cam: str = cam) -> float:
            return last.values.get(name, {}).get(cam, 0.0) - first.values.get(name, {}).get(
                cam, 0.0
            )

        fps = delta(processed) / span
        lags = [
            s.values.get("ivaas_pipeline_frame_lag_seconds", {}).get(cam)
            for s in samples
            if cam in s.values.get("ivaas_pipeline_frame_lag_seconds", {})
        ]
        up = [s.values.get("ivaas_pipeline_stream_up", {}).get(cam, 0.0) for s in samples]
        reconnects = delta("ivaas_pipeline_stream_reconnects_total")
        why = []
        if fps < target_fps:
            why.append(f"{fps:.1f} fps < target {target_fps:g}")
        if not all(v >= 1 for v in up):
            why.append("stream was down during the run")
        if reconnects > 0:
            why.append(f"{reconnects:g} reconnect(s)")
        lag_max = max(lags) if lags else None
        if lag_max is None:
            why.append("no frames processed")
        elif lag_max > max_lag_s:
            why.append(f"lag reached {lag_max:.2f} s > {max_lag_s:g} s")
        results.append(
            CameraResult(
                cam,
                round(fps, 2),
                None if lag_max is None else round(lag_max, 3),
                delta("ivaas_pipeline_frames_dropped_total"),
                reconnects,
                all(v >= 1 for v in up),
                not why,
                why,
            )
        )
    return results


def render(
    results: list[CameraResult], samples: list[Sample], target_fps: float, expected: int | None
) -> tuple[str, bool]:
    span_min = (samples[-1].at - samples[0].at) / 60
    pending = [sum(s.values.get("ivaas_delivery_pending", {}).values()) for s in samples]
    gpus = [s.gpu for s in samples if s.gpu]
    missing = expected is not None and len(results) < expected
    passed = bool(results) and all(r.passed for r in results) and not missing
    lines = [
        "# Edge load test (T2.3)",
        "",
        (
            f"**{'PASS' if passed else 'FAIL'}**: {len(results)} camera(s) over "
            f"{span_min:.1f} min, target {target_fps:g} fps each."
        ),
        "",
    ]
    if missing:
        lines += [f"Expected {expected} cameras but only {len(results)} produced metrics.", ""]
    lines += [
        "| Camera | fps | max lag (s) | dropped frames | reconnects | result |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        verdict = "pass" if r.passed else "FAIL: " + "; ".join(r.why)
        lag = "—" if r.lag_max_s is None else f"{r.lag_max_s:.3f}"
        lines.append(
            f"| {r.camera} | {r.fps:.1f} | {lag} | {r.dropped_frames:g} | {r.reconnects:g} | "
            f"{verdict} |"
        )
    lines += ["", f"Delivery spool: max {max(pending):g} pending, {pending[-1]:g} at the end."]
    if gpus:
        util = [g["gpu_util"] for g in gpus]
        dec = [g["nvdec"] for g in gpus]
        mem = max(g["mem_used_mib"] for g in gpus)
        lines.append(
            f"GPU: utilisation mean {sum(util) / len(util):.0f}% / max {max(util):.0f}%, "
            f"NVDEC mean {sum(dec) / len(dec):.0f}%, memory max {mem:.0f} of "
            f"{gpus[0]['mem_total_mib']:.0f} MiB."
        )
    else:
        lines.append("GPU: not measured (no nvidia-smi on this machine).")
    return "\n".join(lines) + "\n", passed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ivaas_pipeline.loadreport")
    p.add_argument("--metrics", default="http://localhost:9102/metrics")
    p.add_argument("--minutes", type=float, default=10)
    p.add_argument("--every", type=float, default=10, help="seconds between samples")
    p.add_argument("--target-fps", type=float, default=10, help="per camera, after stride")
    p.add_argument("--max-lag", type=float, default=2.0, help="seconds (T3.5: event within 2 s)")
    p.add_argument("--expect", type=int, help="cameras that must all be present, e.g. 17")
    p.add_argument("--out", help="write the markdown report here too")
    p.add_argument(
        "--delivery",
        action="store_true",
        help="print the delivery counters once, as JSON, and exit (for the outage drill)",
    )
    args = p.parse_args(argv)
    if args.delivery:
        print(json.dumps(delivery_counters(httpx.get(args.metrics, timeout=5).text)))
        return 0

    samples: list[Sample] = []
    end = time.monotonic() + args.minutes * 60
    with httpx.Client(timeout=5) as http:
        while True:
            try:
                values = parse(http.get(args.metrics).text)
                samples.append(Sample(time.monotonic(), values, gpu_now()))
            except httpx.HTTPError as exc:
                print(f"metrics unreachable ({type(exc).__name__}); this interval is missing")
            if time.monotonic() >= end:
                break
            time.sleep(args.every)
    if len(samples) < 2:
        print("fewer than two samples: nothing to judge")
        return 2
    results = evaluate(samples, args.target_fps, args.max_lag)
    report, passed = render(results, samples, args.target_fps, args.expect)
    print(report)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(report)
        with open(args.out.rsplit(".", 1)[0] + ".json", "w") as fh:
            json.dump([r.__dict__ for r in results], fh, indent=2)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
