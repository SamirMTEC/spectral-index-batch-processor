"""Security-relevant settings validation tests."""

import pytest
from pydantic import SecretStr, ValidationError
from spectral_common import Settings


def test_settings_reject_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="wildcard CORS"):
        Settings(
            database_url=SecretStr("postgresql+asyncpg://user:password@db/name"),
            redis_url=SecretStr("redis://redis:6379/0"),
            s3_bucket="test",
            cors_origins=["*"],
        )


def test_secret_values_are_not_exposed_by_repr() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql+asyncpg://user:sensitive@db/name"),
        redis_url=SecretStr("redis://redis:6379/0"),
        s3_bucket="test",
    )

    assert "sensitive" not in repr(settings)
    assert settings.database_dsn.endswith("@db/name")


def test_deployed_environment_rejects_dev_auth_and_insecure_cors() -> None:
    with pytest.raises(ValidationError, match="development authentication"):
        Settings(
            database_url=SecretStr("postgresql+asyncpg://user:password@db/name"),
            redis_url=SecretStr("redis://redis:6379/0"),
            s3_bucket="test",
            environment="production",
            dev_auth_enabled=True,
            cors_origins=["https://app.example"],
        )
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            database_url=SecretStr("postgresql+asyncpg://user:password@db/name"),
            redis_url=SecretStr("redis://redis:6379/0"),
            s3_bucket="test",
            environment="production",
            dev_auth_enabled=False,
            cors_origins=["http://app.example"],
        )


@pytest.mark.parametrize(
    "origin",
    [
        "https://user:password@app.example",
        "https://app.example/path",
        "https://app.example?query=1",
        "javascript:alert(1)",
    ],
)
def test_cors_requires_an_exact_http_origin(origin: str) -> None:
    with pytest.raises(ValidationError, match="exact HTTP"):
        Settings(
            database_url=SecretStr("postgresql+asyncpg://user:password@db/name"),
            redis_url=SecretStr("redis://redis:6379/0"),
            s3_bucket="test",
            cors_origins=[origin],
        )


def test_processing_hard_timeout_must_exceed_soft_timeout() -> None:
    with pytest.raises(ValidationError, match="hard limit"):
        Settings(
            database_url=SecretStr("postgresql+asyncpg://user:password@db/name"),
            redis_url=SecretStr("redis://redis:6379/0"),
            s3_bucket="test",
            processing_task_soft_limit_seconds=300,
            processing_task_hard_limit_seconds=300,
        )


def test_deployed_environment_requires_auth0_configuration() -> None:
    with pytest.raises(ValidationError, match="Auth0 domain and audience"):
        Settings(
            database_url=SecretStr("postgresql://user:password@db/name"),
            redis_url=SecretStr("redis://redis:6379/0"),
            s3_bucket="test",
            environment="production",
            dev_auth_enabled=False,
            cors_origins=["https://app.example"],
        )


def test_auth0_domain_is_normalized_and_rejects_urls_with_paths() -> None:
    settings = Settings(
        database_url=SecretStr("postgresql://user:password@db/name"),
        redis_url=SecretStr("redis://redis:6379/0"),
        s3_bucket="test",
        auth0_domain="https://tenant.auth0.com/",
    )
    assert settings.auth0_domain == "tenant.auth0.com"
    assert settings.auth0_issuer == "https://tenant.auth0.com/"
    assert settings.database_dsn.startswith("postgresql+asyncpg://")

    with pytest.raises(ValidationError, match="without path"):
        Settings(
            database_url=SecretStr("postgresql://user:password@db/name"),
            redis_url=SecretStr("redis://redis:6379/0"),
            s3_bucket="test",
            auth0_domain="tenant.auth0.com/oauth",
        )
