"""Test environment configuration loaded before application modules."""

import os

os.environ.setdefault("SPECTRAL_ENVIRONMENT", "test")
os.environ.setdefault(
    "SPECTRAL_DATABASE_URL",
    "postgresql+asyncpg://test_user:test_password@localhost:5432/test_database",
)
os.environ.setdefault("SPECTRAL_REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SPECTRAL_S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("SPECTRAL_S3_REGION", "us-east-1")
os.environ.setdefault("SPECTRAL_S3_BUCKET", "spectral-test")
os.environ.setdefault("SPECTRAL_S3_ACCESS_KEY_ID", "test_access_key")
os.environ.setdefault("SPECTRAL_S3_SECRET_ACCESS_KEY", "test_secret_key")
os.environ.setdefault("SPECTRAL_S3_FORCE_PATH_STYLE", "true")
