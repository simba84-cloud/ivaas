"""The model registry: upload a model version once, deliver it to nodes (M2, T2.6).

An upload is hashed as it streams in, so the digest recorded is the digest of the
bytes stored. Nodes download through their own endpoint, never a public link, and
check that digest before they use a byte of it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from ivaas.adapters.http.auth import current_principal, require
from ivaas.adapters.http.media import files
from ivaas.adapters.http.schemas import ModelVersionOut
from ivaas.adapters.storage.objects import LocalObjectStore
from ivaas.domain.audit import AuditAction
from ivaas.domain.ml_models import (
    MAX_MODEL_BYTES,
    ModelError,
    ModelVersion,
    validate_meta,
    validate_name,
    validate_version,
)
from ivaas.domain.models import NotFoundError
from ivaas.domain.rbac import Permission as P
from ivaas.ports.auth import Principal
from ivaas.tenancy import object_key

Audit = Callable[..., Awaitable[None]]


def add_model_routes(app: FastAPI, get_container: Callable[[Request], Any], audit: Audit) -> None:
    @app.exception_handler(ModelError)
    async def _model_error(_: Request, exc: ModelError) -> Response:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.post(
        "/api/v1/models",
        response_model=ModelVersionOut,
        status_code=201,
        dependencies=[Depends(require(P.DEVICE_CALIBRATE))],
    )
    async def upload_model(
        name: str = Form(...),
        version: str = Form(...),
        meta: str = Form(..., description='the model\'s .json: {"labels": [...], ...}'),
        notes: str = Form(default="", max_length=1000),
        file: UploadFile = File(...),
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> ModelVersionOut:
        """Register a model version. Versions are immutable: a new file is a new version."""
        validate_name(name)
        validate_version(version)
        try:
            metadata = validate_meta(json.loads(meta))
        except json.JSONDecodeError as exc:
            raise ModelError("metadata is not valid JSON") from exc
        if await c.ml_models.find(name, version) is not None:
            raise HTTPException(409, f"{name} {version} already exists; give it a new version")

        digest = hashlib.sha256()
        size = 0

        async def chunks() -> AsyncIterator[bytes]:
            nonlocal size
            head = True
            while chunk := await file.read(4 * 1024 * 1024):
                if head and not chunk.startswith(b"\x08"):
                    # every ONNX file is a protobuf ModelProto, whose first field is ir_version
                    raise ModelError("that is not an ONNX model")
                head = False
                size += len(chunk)
                if size > MAX_MODEL_BYTES:
                    raise HTTPException(413, "model is larger than 512 MB")
                digest.update(chunk)
                yield chunk

        key = object_key(f"models/{name}/{version}-{uuid4().hex[:8]}.onnx")
        await c.objects.put(key, chunks(), "application/octet-stream")
        if size == 0:
            raise ModelError("the model file is empty")
        model = ModelVersion(
            name=name,
            version=version,
            sha256=digest.hexdigest(),
            size_bytes=size,
            object_key=key,
            meta=metadata,
            notes=notes.strip(),
            created_by=principal.name,
            created_at=c.clock.now(),
        )
        await c.ml_models.save(model)
        await audit(
            c,
            principal.name,
            AuditAction.MODEL_UPLOADED,
            model.label,
            sha256=model.sha256,
            size_bytes=size,
        )
        return ModelVersionOut.of(model)

    @app.get(
        "/api/v1/models",
        response_model=list[ModelVersionOut],
        dependencies=[Depends(require(P.TOPOLOGY_READ, scoped=True))],
    )
    async def list_models(c: Any = Depends(get_container)) -> list[ModelVersionOut]:
        return [ModelVersionOut.of(m) for m in await c.ml_models.list_all()]

    @app.get(
        "/api/v1/edge/models/{model_id}/file",
        dependencies=[Depends(require(P.INGEST_WRITE, scoped=True))],
        **files("The ONNX model; its SHA-256 is in X-IVaaS-SHA256", "application/octet-stream"),
    )
    async def download_model(
        model_id: UUID,
        principal: Principal = Depends(current_principal),
        c: Any = Depends(get_container),
    ) -> Response:
        """For enrolled nodes only. The digest travels in a header for the node to check."""
        if not principal.subject.startswith("node:"):
            raise HTTPException(403, "only an enrolled edge node can download a model")
        model = await c.ml_models.get(model_id)
        if model is None:
            raise NotFoundError(f"model {model_id} not found")
        headers = {"X-IVaaS-SHA256": model.sha256}
        if isinstance(c.objects, LocalObjectStore):
            return FileResponse(
                c.objects.path_of(model.object_key),
                media_type="application/octet-stream",
                headers=headers,
            )
        body, _ = await c.objects.open(model.object_key)
        return Response(body, media_type="application/octet-stream", headers=headers)
