"""Model versions: the detectors an edge node runs, delivered over the air (M2, T2.6).

A model version is immutable: a name ("stacks", "layers"), a version label, the ONNX
file's SHA-256 and the metadata its runtime needs (labels, input size). The node
downloads it, checks the digest before using it, and reports which digest it runs,
so "which model counted this load" always has an exact answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
#: an ONNX model for the edge is tens of MB; anything near this is a mistake
MAX_MODEL_BYTES = 512 * 1024 * 1024


class ModelError(ValueError):
    pass


def validate_name(name: str) -> str:
    if not _NAME.match(name):
        raise ModelError("a model name is lowercase letters, digits and hyphens, e.g. stacks")
    return name


def validate_version(version: str) -> str:
    if not _VERSION.match(version):
        raise ModelError("a version is letters, digits, dots, dashes, e.g. v3 or 2026.10.1")
    return version


def validate_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """What the node's runtime reads from <model>.json: labels and input size."""
    labels = meta.get("labels")
    if not isinstance(labels, list) or not labels or not all(isinstance(x, str) for x in labels):
        raise ModelError("metadata needs a non-empty list of class labels")
    for key in ("input_width", "input_height"):
        if not isinstance(meta.get(key), int) or not 16 <= meta[key] <= 4096:
            raise ModelError(f"metadata needs {key} as a whole number of pixels")
    return meta


@dataclass
class ModelVersion:
    name: str
    version: str
    sha256: str
    size_bytes: int
    object_key: str
    meta: dict[str, Any]
    created_by: str
    created_at: datetime
    notes: str = ""
    tenant_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}"
