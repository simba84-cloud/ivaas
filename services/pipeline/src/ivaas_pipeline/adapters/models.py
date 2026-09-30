"""Models over the air, on the node (proposal M2, T2.6).

A registered model arrives as a reference: name, version, SHA-256, size, metadata
and a download URL. The node keeps a cache keyed by digest
(/var/lib/ivaas/models/<sha256>/model.onnx + model.json), so a model downloaded once
is never downloaded again, and a model whose bytes do not match their digest is
never used: it is refused before it replaces anything.

Switching models does not restart anything. Each camera's detector and layer
counter sit behind a `Swappable`; the new model is loaded and run once on a blank
frame *beside* the old one, and only then swapped in, between two frames. If any
step fails the old model keeps running and the node reports why.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from ivaas_pipeline.types import Box, Frame

log = logging.getLogger(__name__)

DEFAULT_CACHE = "/var/lib/ivaas/models"


class ModelFetchError(RuntimeError):
    pass


def describe(ref: dict | str | None) -> dict | None:
    """What the heartbeat says is running: name, version and digest, or the local path."""
    if ref is None:
        return None
    if isinstance(ref, str):
        return {"path": ref}
    if "sha256" in ref:
        return {"name": ref.get("name"), "version": ref.get("version"), "sha256": ref["sha256"]}
    return {"path": ref.get("path")}


def label(ref: dict | str | None) -> str:
    d = describe(ref) or {}
    return f"{d['name']} {d['version']}" if d.get("name") else str(d.get("path"))


class ModelCache:
    def __init__(
        self, client: httpx.Client | None, root: str | Path = DEFAULT_CACHE, keep: int = 4
    ) -> None:
        self._client = client
        self._root = Path(root)
        self._keep = keep
        self._lock = threading.Lock()

    def resolve(self, ref: dict | str) -> str:
        """A local path to the model's .onnx, next to its .json. Downloads and verifies a
        registered model the first time; a plain path is used as it is."""
        if isinstance(ref, str):
            return ref
        if "sha256" not in ref:
            return ref["path"]
        digest = ref["sha256"]
        target = self._root / digest / "model.onnx"
        with self._lock:
            if target.is_file():
                return str(target)
            self._fetch(ref, target.parent)
            self._prune(keep=digest)
        return str(target)

    def _fetch(self, ref: dict, into: Path) -> None:
        if self._client is None:
            raise ModelFetchError("this node has no API client to download models with")
        self._root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=self._root, prefix=".incoming-"))
        try:
            sha = hashlib.sha256()
            size = 0
            with self._client.stream("GET", ref["url"]) as r:
                if r.status_code != 200:
                    raise ModelFetchError(f"download refused: HTTP {r.status_code}")
                with open(staging / "model.onnx", "wb") as fh:
                    for chunk in r.iter_bytes(1024 * 1024):
                        sha.update(chunk)
                        size += len(chunk)
                        fh.write(chunk)
            if sha.hexdigest() != ref["sha256"]:
                raise ModelFetchError(
                    f"checksum mismatch: got {sha.hexdigest()[:12]}, expected {ref['sha256'][:12]}"
                )
            if ref.get("size_bytes") not in (None, size):
                raise ModelFetchError(f"size mismatch: {size} bytes, expected {ref['size_bytes']}")
            (staging / "model.json").write_text(json.dumps(ref.get("meta") or {}))
            staging.rename(into)  # the cache only ever holds verified models
            log.info("model %s cached (%d bytes, sha256 %s)", label(ref), size, ref["sha256"][:12])
        except httpx.HTTPError as exc:
            raise ModelFetchError(f"download failed: {type(exc).__name__}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _prune(self, keep: str) -> None:
        """Keep the newest few: the current model and the ones a rollback would need."""
        dirs = sorted(
            (d for d in self._root.iterdir() if d.is_dir() and not d.name.startswith(".")),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for d in dirs[self._keep :]:
            if d.name != keep:
                shutil.rmtree(d, ignore_errors=True)


class Swappable:
    """Delegates to a model that can be replaced between two frames.

    Attribute assignment is atomic in CPython, and each camera thread reads `inner`
    once per frame, so a frame is always processed wholly by one model or the other.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def replace(self, new: Any) -> None:
        self.inner = new

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    # the two ports the pipeline calls, spelled out so they never hit __getattr__
    def detect(self, frame: Frame):
        return self.inner.detect(frame)

    def estimate(self, image: np.ndarray, box: Box):
        return self.inner.estimate(image, box)


def smoke_test_detector(detector: Any) -> None:
    """Run once on a blank frame: a model that loads but cannot infer is refused here,
    not discovered on the next truck."""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    detector.detect(Frame("smoke-test", image, datetime.now(UTC)))


def smoke_test_layers(counter: Any) -> None:
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    counter.estimate(image, Box(100, 100, 400, 600))


class ModelSwitcher:
    """Moves every camera onto a new model, all or nothing."""

    def __init__(
        self,
        cache: ModelCache,
        make_detector: Callable[[str], Any],
        make_layers: Callable[[str], Any] | None,
    ) -> None:
        self._cache = cache
        self._make_detector = make_detector
        self._make_layers = make_layers

    def switch(
        self,
        detectors: list[Swappable],
        layers: list[Swappable],
        detector_ref: Any,
        layers_ref: Any | None,
    ) -> None:
        """Load and test the new models for every camera first; swap only if all load."""
        new_detectors = []
        if detector_ref is not None:
            path = self._cache.resolve(detector_ref)
            for _ in detectors:
                d = self._make_detector(path)
                smoke_test_detector(d)
                new_detectors.append(d)
        new_layers = []
        if layers_ref is not None and self._make_layers is not None and layers:
            path = self._cache.resolve(layers_ref)
            for _ in layers:
                counter = self._make_layers(path)
                smoke_test_layers(counter)
                new_layers.append(counter)
        # everything loaded: now, and only now, the running pipeline changes
        for slot, new in zip(detectors, new_detectors, strict=False):
            slot.replace(new)
        for slot, new in zip(layers, new_layers, strict=False):
            slot.replace(new)


MODEL_KEYS = ("model", "layers_model", "layers_model_ref", "config_version", "configured")


def only_models_changed(old: dict, new: dict) -> bool:
    """True when a new configuration differs from the running one only in its models:
    that can be applied without touching a stream. Anything else needs a restart."""

    def rest(cfg: dict) -> dict:
        return {k: v for k, v in cfg.items() if k not in MODEL_KEYS}

    def arch(cfg: dict) -> str:
        return (cfg.get("model") or {}).get("arch", "rtdetr")

    # a different detector family, or gaining or losing a layer model, changes how the
    # pipeline is built, not just which weights it loads
    return (
        rest(old) == rest(new)
        and arch(old) == arch(new)
        and bool(layers_ref(old)) == bool(layers_ref(new))
    )


def layers_ref(cfg: dict) -> dict | str | None:
    return cfg.get("layers_model_ref") or cfg.get("layers_model")


class ConfigApplier:
    """What a node does when the API says its configuration changed.

    A change to the models alone is applied in place: fetched, verified, loaded,
    tested, swapped, with every stream left running. Any other change restarts the
    node. A model that fails is recorded and not retried until the configuration
    changes again; the node keeps counting with the model it had.
    """

    def __init__(
        self,
        *,
        current: dict,
        fetch: Callable[[], dict],
        switcher: ModelSwitcher,
        detectors: list[Swappable],
        layers: list[Swappable],
        adopt: Callable[[str], None],
        restart: Callable[[], None],
    ) -> None:
        self.current = current
        self.model_error: str | None = None
        self._fetch, self._switcher = fetch, switcher
        self._detectors, self._layers = detectors, layers
        self._adopt, self._restart = adopt, restart
        self._failed: set[str] = set()
        self._busy = threading.Lock()

    def status(self) -> dict:
        """For the heartbeat: the models running now, and the last one refused."""
        return {
            "models": {
                role: d
                for role, d in (
                    ("detector", describe(self.current.get("model"))),
                    ("layers", describe(layers_ref(self.current))),
                )
                if d
            },
            "model_error": self.model_error,
        }

    def on_new_version(self, version: str) -> None:
        """Called from the heartbeat thread; the work happens on its own thread, so a
        large download never silences the heartbeat."""
        if version in self._failed or self._busy.locked():
            return
        threading.Thread(target=self.apply, name="config-apply", daemon=True).start()

    def apply(self) -> None:
        if not self._busy.acquire(blocking=False):
            return
        try:
            new = self._fetch()
            version = new.get("config_version", "")
            if version in self._failed or version == self.current.get("config_version"):
                return
            if not only_models_changed(self.current, new):
                log.info("configuration %s changes more than models; restarting onto it", version)
                self._restart()
                return
            old = label(self.current.get("model"))
            try:
                started = time.monotonic()
                self._switcher.switch(
                    self._detectors,
                    self._layers,
                    new.get("model"),
                    layers_ref(new),
                )
            except Exception as exc:  # checksum, download, load or inference: all refuse
                self._failed.add(version)
                self.model_error = f"{label(new.get('model'))}: {exc}; kept {old}"
                log.exception("model switch refused: %s", self.model_error)
                return
            self.current, self.model_error = new, None
            self._adopt(version)
            log.info(
                "switched to %s in %.1f s without restarting a stream",
                label(new.get("model")),
                time.monotonic() - started,
            )
        finally:
            self._busy.release()
