"""Models over the air on the node (proposal M2, T2.6): verified, in place, all or nothing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from ivaas_pipeline.adapters import models
from ivaas_pipeline.adapters.models import (
    ConfigApplier,
    ModelCache,
    ModelFetchError,
    ModelSwitcher,
    Swappable,
    only_models_changed,
)

V1, V2 = b"\x08model-one", b"\x08model-two"
META = {"labels": ["stack"], "input_width": 640, "input_height": 640}


def ref(data: bytes, version="v1", sha=None) -> dict:
    return {
        "name": "stacks",
        "version": version,
        "sha256": sha or hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "url": f"/api/v1/edge/models/{version}/file",
        "meta": META,
    }


def serving(files: dict[str, bytes], calls: list | None = None) -> httpx.Client:
    def api(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        version = request.url.path.split("/")[-2]
        return httpx.Response(200, content=files[version])

    return httpx.Client(base_url="http://api", transport=httpx.MockTransport(api))


def test_a_model_is_downloaded_once_verified_and_kept_with_its_metadata(tmp_path):
    calls: list = []
    cache = ModelCache(serving({"v1": V1}, calls), tmp_path)
    path = cache.resolve(ref(V1))
    assert Path(path).read_bytes() == V1
    assert (tmp_path / hashlib.sha256(V1).hexdigest() / "model.json").read_text().count("stack")
    assert cache.resolve(ref(V1)) == path
    assert len(calls) == 1, "a cached model must not be downloaded again"


def test_a_model_whose_bytes_do_not_match_is_never_cached_or_used(tmp_path):
    cache = ModelCache(serving({"v1": b"\x08tampered"}), tmp_path)
    with pytest.raises(ModelFetchError, match="checksum mismatch"):
        cache.resolve(ref(V1))
    assert [p.name for p in tmp_path.iterdir()] == []  # no partial download left behind


def test_a_local_path_is_used_as_it_is(tmp_path):
    cache = ModelCache(None, tmp_path)
    assert cache.resolve({"path": "/models/stacks-v2.onnx"}) == "/models/stacks-v2.onnx"
    assert cache.resolve("/models/layers-v3.onnx") == "/models/layers-v3.onnx"


def test_the_cache_keeps_only_the_newest_few(tmp_path):
    blobs = {f"v{i}": b"\x08" + bytes([i]) * 10 for i in range(6)}
    cache = ModelCache(serving(blobs), tmp_path, keep=3)
    for v, data in blobs.items():
        cache.resolve(ref(data, v))
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 3


class FakeDetector:
    def __init__(self, path: str, fail: bool = False):
        self.path, self.fail = path, fail

    def detect(self, frame):
        if self.fail:
            raise RuntimeError("model cannot infer")
        return [self.path]


def test_a_swapped_model_takes_the_next_frame():
    slot = Swappable(FakeDetector("old"))
    assert slot.detect(None) == ["old"]
    slot.replace(FakeDetector("new"))
    assert slot.detect(None) == ["new"]


def test_a_switch_is_all_or_nothing(tmp_path):
    cache = ModelCache(serving({"v2": V2}), tmp_path)
    made = []

    def make(path):
        made.append(path)
        return FakeDetector(path, fail=len(made) == 2)  # the second camera's copy fails

    slots = [Swappable(FakeDetector("old")), Swappable(FakeDetector("old"))]
    with pytest.raises(RuntimeError):
        ModelSwitcher(cache, make, None).switch(slots, [], ref(V2, "v2"), None)
    assert [s.detect(None) for s in slots] == [["old"], ["old"]], "one camera switched alone"


def test_which_changes_can_be_applied_in_place():
    base = {
        "config_version": "a",
        "model": {"version_id": "1", "arch": "rtdetr"},
        "cameras": [{"key": "c", "zone": [0, 0, 1, 1]}],
    }
    assert only_models_changed(base, {**base, "config_version": "b", "model": {"version_id": "2"}})
    moved = {**base, "cameras": [{"key": "c", "zone": [0, 0, 2, 2]}]}
    assert not only_models_changed(base, moved)
    yolo = {**base, "model": {"version_id": "2", "arch": "yolo"}}
    assert not only_models_changed(base, yolo)
    with_layers = {**base, "layers_model_ref": {"sha256": "x"}}
    assert not only_models_changed(base, with_layers)


def applier(tmp_path, new_cfg, make=FakeDetector):
    cache = ModelCache(serving({"v1": V1, "v2": V2}), tmp_path)
    slots = [Swappable(FakeDetector("old"))]
    adopted, restarted = [], []
    a = ConfigApplier(
        current={"config_version": "c1", "model": ref(V1), "cameras": []},
        fetch=lambda: new_cfg,
        switcher=ModelSwitcher(cache, make, None),
        detectors=slots,
        layers=[],
        adopt=adopted.append,
        restart=lambda: restarted.append(True),
    )
    return a, slots, adopted, restarted


def test_a_new_model_is_switched_in_without_a_restart(tmp_path):
    new = {"config_version": "c2", "model": ref(V2, "v2"), "cameras": []}
    a, slots, adopted, restarted = applier(tmp_path, new)
    a.apply()
    assert adopted == ["c2"] and restarted == []
    assert slots[0].detect(None)[0].endswith("model.onnx")
    assert a.status() == {
        "models": {"detector": {"name": "stacks", "version": "v2", "sha256": ref(V2)["sha256"]}},
        "model_error": None,
    }


def test_a_bad_model_is_refused_the_old_one_keeps_counting_and_it_is_not_retried(tmp_path):
    new = {"config_version": "c2", "model": ref(V2, "v2", sha="0" * 64), "cameras": []}
    a, slots, adopted, restarted = applier(tmp_path, new)
    a.apply()
    assert slots[0].detect(None) == ["old"] and adopted == [] and restarted == []
    assert (
        "checksum mismatch" in a.status()["model_error"]
        and "kept stacks v1" in a.status()["model_error"]
    )
    a.on_new_version("c2")  # the heartbeat keeps seeing the version: no second attempt
    assert "c2" in a._failed


def test_a_model_that_loads_but_cannot_infer_is_refused(tmp_path):
    new = {"config_version": "c2", "model": ref(V2, "v2"), "cameras": []}
    a, slots, adopted, _ = applier(tmp_path, new, make=lambda p: FakeDetector(p, fail=True))
    a.apply()
    assert slots[0].detect(None) == ["old"] and adopted == []
    assert "cannot infer" in a.status()["model_error"]


def test_any_other_change_restarts_the_node(tmp_path):
    new = {"config_version": "c2", "model": ref(V1), "cameras": [{"key": "new"}]}
    a, _, adopted, restarted = applier(tmp_path, new)
    a.apply()
    assert restarted == [True] and adopted == []


def test_describe_names_what_runs():
    assert models.describe(ref(V1))["version"] == "v1"
    assert models.describe({"path": "/m.onnx"}) == {"path": "/m.onnx"}
    assert models.describe(None) is None


MODELS = Path(__file__).resolve().parents[3] / "models"
REAL = [MODELS / "stacks-v1.onnx", MODELS / "stacks-v2.onnx", MODELS / "layers-v3.onnx"]


@pytest.mark.skipif(not all(p.exists() for p in REAL), reason="ONNX models not present")
def test_real_models_are_fetched_verified_loaded_and_swapped(tmp_path):
    """stacks-v1 -> stacks-v2 and a layer model, through the cache, with ONNX Runtime."""
    import json

    from ivaas_pipeline.adapters.onnx_layers import OnnxLayerCounter
    from ivaas_pipeline.adapters.onnx_rtdetr import OnnxRtDetrDetector

    def registered(path: Path, version: str) -> tuple[dict, bytes]:
        data = path.read_bytes()
        meta = json.loads(path.with_suffix(".json").read_text())
        r = ref(data, version) | {"meta": meta, "name": path.stem.split("-")[0]}
        return r, data

    v1, d1 = registered(REAL[0], "v1")
    v2, d2 = registered(REAL[1], "v2")
    lay, dl = registered(REAL[2], "l3")
    cache = ModelCache(serving({"v1": d1, "v2": d2, "l3": dl}), tmp_path)
    detector = Swappable(OnnxRtDetrDetector(cache.resolve(v1)))
    layers = Swappable(OnnxLayerCounter(cache.resolve(lay)))
    before = detector.inner
    ModelSwitcher(cache, OnnxRtDetrDetector, OnnxLayerCounter).switch([detector], [layers], v2, lay)
    assert detector.inner is not before and layers.inner is not None
    assert Path(cache.resolve(v2)).read_bytes() == d2  # the verified copy, not the source
    models.smoke_test_detector(detector)  # the swapped-in model infers


def test_a_long_runtime_error_is_bounded_so_the_heartbeat_is_never_rejected(tmp_path):
    long = "x" * 5000

    def make(path):
        raise RuntimeError(long)

    new = {"config_version": "c2", "model": ref(V2, "v2"), "cameras": []}
    a, *_ = applier(tmp_path, new, make=make)
    a.apply()
    assert len(a.status()["model_error"]) < 400  # the API accepts up to 500
