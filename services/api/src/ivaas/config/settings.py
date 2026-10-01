from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IVAAS_", env_file=".env", extra="ignore")

    storage: Literal["memory", "postgres"] = "memory"
    database_url: str = "postgresql+asyncpg://ivaas:ivaas@localhost:5432/ivaas"
    events: Literal["memory", "nats"] = "memory"
    nats_url: str = "nats://localhost:4222"
    reconcile_tolerance: float = 0.95
    # A confirmed LPR read at an idle bay opens a session in this direction; "" disables.
    auto_open_direction: str = "loading"
    # A session whose plate has not been read for this long is closed automatically; 0 disables.
    auto_close_idle_minutes: float = 10
    # Uploaded-video analysis. Object storage: "s3" (MinIO in docker-compose) or "local".
    objects: Literal["local", "s3"] = "local"
    objects_dir: str = "/tmp/ivaas-objects"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "ivaas"
    s3_secret_key: str = "ivaas-secret"
    s3_bucket: str = "ivaas"
    stack_model: str = "../../models/stacks-v2.onnx"
    layers_model: str = "../../models/layers-v3.onnx"
    # Face recognition (off until an admin records its legal basis under Settings).
    # OpenCV Zoo models: YuNet (MIT) finds faces, SFace (Apache-2.0) embeds them.
    face_detector_model: str = "../../models/face_detection_yunet_2023mar.onnx"
    face_recognizer_model: str = "../../models/face_recognition_sface_2021dec.onnx"
    max_upload_mb: int = 5120
    # Run the video-analysis worker inside this process. True keeps a single-process
    # deployment working; docker-compose sets it False on the API and runs a separate
    # worker, so a long CPU-bound analysis cannot starve the portal's requests.
    run_analysis_worker: bool = True
    # Signs the short-lived links that let <img>/<video> load report objects without a token.
    object_link_secret: str = "dev-only-object-link-secret-change-me"
    cors_origins: list[str] = ["http://localhost:5173"]
    seed_demo_data: bool = True
    # empty = no media server (dev): cameras are stored but no stream is provisioned
    mediamtx_api_url: str = ""
    # cameras whose video is evidence: recorded at the edge, clips cut around each count
    evidence_roles: list[str] = ["chokepoint", "lpr"]
    # where the API reads a camera's stream for a still frame (drawing security zones)
    media_rtsp_url: str = "rtsp://localhost:8554"
    # Fernet keys for camera credentials at rest, newest first. Generate one with
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secrets_keys: list[str] = []
    # Webhooks go to public https receivers only. True also allows http and addresses
    # on the deployment's own network (an ERP on the site LAN): never on a shared host.
    webhook_allow_private: bool = False
    # The price book billing rates with (SKUs, plans, tax, currency). The one shipped
    # is made up and says so: every invoice from it is stamped NOT FOR ISSUE.
    price_book: str = str(Path(__file__).parent / "price_book.placeholder.json")
    # Analysis assistant. Any OpenAI-compatible server; empty = assistant disabled.
    llm_url: str = ""
    llm_model: str = "qwen3:8b"  # Apache-2.0 open weights, reliable tool calling
    llm_api_key: str = ""
    # Authentication. "oidc" for any OpenID Connect provider (Keycloak in docker-compose);
    # "local" mints HS256 tokens for the demo users below: development only.
    auth_mode: Literal["local", "oidc"] = "local"
    auth_local_secret: str = "dev-only-change-me-please-32chars"
    oidc_issuer: str = "http://localhost:8180/realms/ivaas"
    oidc_audience: str = "ivaas-portal"
    # {api key: caller name}; the pipeline posts crossings with X-IVaaS-Key
    service_api_keys: dict[str, str] = {"dev-pipeline-key": "pipeline"}
    # local mode only: username -> (password, role)
    local_users: dict[str, tuple[str, str]] = {
        "admin": ("admin", "admin"),
        "operator": ("operator", "operator"),
        "viewer": ("viewer", "viewer"),
    }
