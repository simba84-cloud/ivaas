from __future__ import annotations

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
    cors_origins: list[str] = ["http://localhost:5173"]
    seed_demo_data: bool = True
    # empty = no media server (dev): cameras are stored but no stream is provisioned
    mediamtx_api_url: str = ""
    # Fernet keys for camera credentials at rest, newest first. Generate one with
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secrets_keys: list[str] = []
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
