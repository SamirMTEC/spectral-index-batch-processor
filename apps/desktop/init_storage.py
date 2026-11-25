"""Create the private, versioned local bucket without a separate mc image."""

from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError


def main() -> None:
    bucket = os.environ["SPECTRAL_S3_BUCKET"]
    client = boto3.client(
        "s3",
        endpoint_url="http://minio:9000",
        region_name="us-east-1",
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
    )
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket"}:
            raise
        client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(
        Bucket=bucket,
        VersioningConfiguration={"Status": "Enabled"},
    )
    try:
        client.delete_bucket_policy(Bucket=bucket)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {
            "NoSuchBucketPolicy",
            "NoSuchBucket",
            "404",
        }:
            raise


if __name__ == "__main__":
    main()
