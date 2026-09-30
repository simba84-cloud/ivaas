"""The simulator's control side and the T2.3 load report."""

from __future__ import annotations

from ivaas_pipeline import loadreport, sim
from ivaas_pipeline.loadreport import Sample


def test_the_plan_is_the_scopes_camera_array():
    plan = sim.camera_plan(16, 1)
    roles = [role for _, role in plan]
    assert len(plan) == 17
    assert roles.count("overhead") == 4 and roles.count("side_high") == 4
    assert roles.count("side_mid") == 4 and roles.count("side_low") == 2
    assert roles.count("chokepoint") == 2 and roles.count("lpr") == 1
    assert all(name.startswith(sim.PREFIX) for name, _ in plan)  # teardown finds them
    assert len({name for name, _ in plan}) == 17


def test_a_bigger_rig_cycles_the_layout():
    assert len(sim.camera_plan(24, 2)) == 26


def test_the_node_is_given_every_simulated_camera():
    cams = [{"id": f"c{i}", "role": role} for i, (_, role) in enumerate(sim.camera_plan(16, 1))]
    cfg = sim.node_config(cams, model="/models/m.onnx", layers=None, stride=2)
    assert len(cfg["cameras"]) == 16 and len(cfg["lpr_cameras"]) == 1
    assert all(c["zone"] and c["stride"] == 2 for c in cfg["cameras"])
    assert "layers_model" not in cfg


def metrics(t, cams, frames, up=1.0, lag=0.3, reconnects=0.0, pending=0.0):
    return Sample(
        t,
        {
            "ivaas_pipeline_frames_processed_total": {c: frames for c in cams},
            "ivaas_pipeline_stream_up": {c: up for c in cams},
            "ivaas_pipeline_frame_lag_seconds": {c: lag for c in cams},
            "ivaas_pipeline_stream_reconnects_total": {c: reconnects for c in cams},
            "ivaas_delivery_pending": {"": pending},
        },
    )


def test_a_node_keeping_up_passes():
    samples = [metrics(0, ["a", "b"], 0), metrics(60, ["a", "b"], 900)]  # 15 fps
    results = loadreport.evaluate(samples, target_fps=10, max_lag_s=2)
    report, passed = loadreport.render(results, samples, 10, expected=2)
    assert passed and all(r.fps == 15 for r in results)
    assert report.startswith("# Edge load test (T2.3)\n\n**PASS**")


def test_slow_dropped_or_missing_cameras_fail_and_say_why():
    samples = [metrics(0, ["a"], 0), metrics(60, ["a"], 300, reconnects=1.0)]  # 5 fps
    samples[1].values["ivaas_pipeline_stream_up"]["b"] = 0.0  # b never produced a frame
    results = {r.camera: r for r in loadreport.evaluate(samples, 10, 2)}
    assert not results["a"].passed and "5.0 fps < target 10" in results["a"].why[0]
    assert "1 reconnect(s)" in results["a"].why
    assert not results["b"].passed and "no frames processed" in results["b"].why
    _, passed = loadreport.render(list(results.values()), samples, 10, expected=17)
    assert not passed


def test_lag_beyond_the_budget_fails():
    samples = [metrics(0, ["a"], 0), metrics(60, ["a"], 900, lag=3.5)]
    [r] = loadreport.evaluate(samples, 10, max_lag_s=2)
    assert not r.passed and "lag reached 3.50 s" in r.why[0]


def test_delivery_counters_are_read_from_the_exposition():
    text = """# TYPE ivaas_delivery_queued_total counter
ivaas_delivery_queued_total{path="/api/v1/ingest/crossings"} 7.0
ivaas_delivery_queued_total{path="/api/v1/ingest/plates"} 3.0
# TYPE ivaas_delivery_delivered_total counter
ivaas_delivery_delivered_total{path="/api/v1/ingest/crossings"} 6.0
# TYPE ivaas_delivery_dropped_total counter
ivaas_delivery_dropped_total{reason="overflow"} 0.0
# TYPE ivaas_delivery_pending gauge
ivaas_delivery_pending 4.0
"""
    assert loadreport.delivery_counters(text) == {
        "queued": 10.0,
        "delivered": 6.0,
        "dropped": 0.0,
        "pending": 4.0,
    }
