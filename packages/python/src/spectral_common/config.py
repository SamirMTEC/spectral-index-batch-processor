"""Typed environment configuration shared by the API and worker."""

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from ``SPECTRAL_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="SPECTRAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    software_version: str = "0.1.0"

    database_url: SecretStr
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=5, ge=0, le=100)
    database_pool_timeout_seconds: int = Field(default=10, ge=1, le=120)
    database_pool_recycle_seconds: int = Field(default=300, ge=30, le=3600)
    redis_url: SecretStr
    redis_stream: str = "spectral:jobs"
    redis_consumer_group: str = "spectral-workers"

    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_force_path_style: bool = False
    storage_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    storage_read_timeout_seconds: int = Field(default=60, ge=1, le=600)
    storage_max_connections: int = Field(default=20, ge=1, le=200)
    upload_multipart_threshold_bytes: int = Field(default=16 * 1024 * 1024, ge=5 * 1024 * 1024)
    upload_part_size_bytes: int = Field(default=8 * 1024 * 1024, ge=5 * 1024 * 1024)
    upload_url_expiry_seconds: int = Field(default=900, ge=60, le=3600)
    upload_session_expiry_seconds: int = Field(default=86_400, ge=300, le=604_800)
    upload_max_files_per_session: int = Field(default=10_000, ge=1, le=100_000)
    upload_max_file_bytes: int = Field(default=50 * 1024**3, ge=1)
    upload_max_session_bytes: int = Field(default=100 * 1024**3, ge=1)
    upload_max_dataset_bytes: int = Field(default=1024**4, ge=1)
    zip_max_entries: int = Field(default=10_000, ge=1, le=100_000)
    zip_max_entry_bytes: int = Field(default=50 * 1024**3, ge=1)
    zip_max_uncompressed_bytes: int = Field(default=100 * 1024**3, ge=1)
    zip_max_compression_ratio: float = Field(default=200.0, ge=1.0, le=10_000.0)
    zip_max_path_depth: int = Field(default=16, ge=1, le=128)
    raster_max_pixels: int = Field(default=500_000_000, ge=1)
    raster_max_bands: int = Field(default=64, ge=1, le=4096)
    raster_max_decoded_bytes: int = Field(default=64 * 1024**3, ge=1)
    api_max_request_bytes: int = Field(default=16 * 1024**2, ge=1024)
    api_request_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    api_workers: int = Field(default=2, ge=1, le=32)
    api_keepalive_seconds: int = Field(default=5, ge=1, le=120)
    graceful_shutdown_seconds: int = Field(default=45, ge=5, le=300)
    readiness_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    api_rate_limit_requests: int = Field(default=600, ge=1)
    upload_rate_limit_requests: int = Field(default=60, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    processing_concurrency: int = Field(default=2, ge=1, le=64)
    processing_task_soft_limit_seconds: int = Field(default=900, ge=30, le=86_400)
    processing_task_hard_limit_seconds: int = Field(default=930, ge=31, le=86_430)
    processing_max_tasks_per_child: int = Field(default=25, ge=1, le=10_000)

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    dev_auth_enabled: bool | None = None
    auth0_domain: str | None = None
    auth0_audience: str | None = None

    @field_validator("cors_origins")
    @classmethod
    def reject_wildcard_cors(cls, origins: list[str]) -> list[str]:
        """Credentialed application traffic must never use wildcard CORS."""
        normalized: list[str] = []
        for origin in origins:
            parsed = urlsplit(origin)
            if origin == "*":
                raise ValueError("wildcard CORS origins are not allowed")
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError(f"CORS origin must be an exact HTTP(S) origin: {origin}")
            normalized.append(origin.rstrip("/"))
        if len(normalized) != len(set(normalized)):
            raise ValueError("duplicate CORS origins are not allowed")
        return normalized

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalized

    @field_validator("auth0_domain")
    @classmethod
    def normalize_auth0_domain(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().removeprefix("https://").rstrip("/")
        parsed = urlsplit(f"https://{normalized}")
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Auth0 domain must be a hostname without path or credentials")
        return normalized

    @model_validator(mode="after")
    def enforce_deployed_security_defaults(self) -> Self:
        if self.environment in {"staging", "production"}:
            if self.dev_auth_enabled is True:
                raise ValueError("development authentication cannot be enabled when deployed")
            if any(not origin.startswith("https://") for origin in self.cors_origins):
                raise ValueError("deployed CORS origins must use HTTPS")
            if not self.auth0_domain or not self.auth0_audience:
                raise ValueError("Auth0 domain and audience are required when deployed")
        if self.upload_max_session_bytes > self.upload_max_dataset_bytes:
            raise ValueError("upload session limit cannot exceed the dataset limit")
        if self.zip_max_entry_bytes > self.zip_max_uncompressed_bytes:
            raise ValueError("ZIP entry limit cannot exceed the ZIP expansion limit")
        if self.processing_task_hard_limit_seconds <= self.processing_task_soft_limit_seconds:
            raise ValueError("processing hard limit must be greater than the soft limit")
        return self

    @property
    def database_dsn(self) -> str:
        dsn = self.database_url.get_secret_value()
        if dsn.startswith("postgres://"):
            return dsn.replace("postgres://", "postgresql+asyncpg://", 1)
        if dsn.startswith("postgresql://"):
            return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        return dsn

    @property
    def redis_dsn(self) -> str:
        return self.redis_url.get_secret_value()

    @property
    def s3_secret(self) -> str | None:
        if self.s3_secret_access_key is None:
            return None
        return self.s3_secret_access_key.get_secret_value()

    @property
    def development_auth_enabled(self) -> bool:
        """Allow the local identity bridge only outside deployed environments."""
        if self.dev_auth_enabled is not None:
            return self.dev_auth_enabled
        return self.environment in {"development", "test"}

    @property
    def auth0_issuer(self) -> str | None:
        if self.auth0_domain is None:
            return None
        return f"https://{self.auth0_domain}/"


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""
    return Settings()  # type: ignore[call-arg]
