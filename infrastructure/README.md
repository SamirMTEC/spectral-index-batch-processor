# Local infrastructure

`docker-compose.yml` provisions PostgreSQL, Redis, MinIO, the API, and the worker. The `minio-init` one-shot service creates a private, versioned bucket.

The storage client uses the S3 API. Production can point the same settings at Cloudflare R2 or another S3-compatible provider by changing the endpoint, region, bucket, and workload credentials. Production credentials must come from the deployment secret manager, never from an image or repository file.
