"""Owner-scoped direct multipart upload endpoints."""

import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from spectral_common import Settings, get_settings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import (
    AssetRole,
    BandAsset,
    BandAssetState,
    Dataset,
    DatasetState,
    UploadMode,
    UploadObject,
    UploadObjectState,
    UploadSession,
    UploadSessionState,
)
from spectral_api.routes.datasets import get_owned_dataset
from spectral_api.schemas.uploads import (
    UploadComplete,
    UploadObjectResponse,
    UploadPartRequest,
    UploadPartUrl,
    UploadPartUrls,
    UploadPlan,
    UploadSessionCreate,
    UploadSessionCreated,
    UploadSessionResponse,
)
from spectral_api.services.uploads import (
    UploadPlanData,
    UploadStorage,
    classify_extension,
    mark_invalid,
    quarantine_key,
    sanitize_filename,
)

router = APIRouter(tags=["uploads"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _require_desktop_mode() -> None:
    if os.environ.get("SPECTRAL_DESKTOP_MODE") != "true":
        raise HTTPException(status_code=404, detail="Desktop operation unavailable")


def storage_for(request: Request, settings: Settings) -> UploadStorage:
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise RuntimeError("Application resources are not initialized")
    return UploadStorage(resources.s3, resources.s3_presign, settings)


async def get_owned_upload_session(
    session: AsyncSession, upload_session_id: UUID, owner_id: UUID
) -> UploadSession:
    value = await session.scalar(
        select(UploadSession).where(
            UploadSession.id == upload_session_id,
            UploadSession.__table__.c.owner_id == owner_id,
        )
    )
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return value


async def get_owned_upload_object(
    session: AsyncSession, upload_session_id: UUID, object_id: UUID, owner_id: UUID
) -> UploadObject:
    value = await session.scalar(
        select(UploadObject).where(
            UploadObject.id == object_id,
            UploadObject.upload_session_id == upload_session_id,
            UploadObject.owner_id == owner_id,
        )
    )
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload object not found")
    return value


def plan_response(upload_object: UploadObject, plan: UploadPlanData | None) -> UploadPlan:
    created_at = upload_object.__dict__.get("created_at") or datetime.now(UTC)
    data: dict[str, Any] = {
        "id": upload_object.id,
        "upload_session_id": upload_object.upload_session_id,
        "filename": upload_object.filename,
        "client_relative_path": upload_object.client_relative_path,
        "extension": upload_object.extension,
        "declared_size": upload_object.declared_size,
        "observed_size": upload_object.observed_size,
        "role": upload_object.role,
        "state": upload_object.state,
        "client_checksum": upload_object.client_checksum,
        "verified_checksum": upload_object.verified_checksum,
        "failure_code": upload_object.failure_code,
        "failure_message": upload_object.failure_message,
        "metadata_json": upload_object.metadata_json,
        "created_at": created_at,
        "updated_at": upload_object.__dict__.get("updated_at") or created_at,
    }
    if plan is not None:
        data.update(
            method=plan.method,
            upload_url=plan.upload_url,
            part_size=plan.part_size,
            part_count=plan.part_count,
        )
    return UploadPlan.model_validate(data)


@router.post(
    "/datasets/{dataset_id}/uploads",
    response_model=UploadSessionCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_session(
    dataset_id: UUID,
    payload: UploadSessionCreate,
    request: Request,
    session: Session,
    user: CurrentUser,
) -> UploadSessionCreated:
    settings = get_settings()
    dataset = await session.scalar(
        select(Dataset)
        .where(Dataset.id == dataset_id, Dataset.owner_id == user.id)
        .with_for_update()
    )
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if dataset.state in {DatasetState.ARCHIVED, DatasetState.READY}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dataset is read-only")
    if len(payload.files) > settings.upload_max_files_per_session:
        raise HTTPException(status_code=413, detail="Too many files in one upload session")
    total_bytes = sum(item.size_bytes for item in payload.files)
    if total_bytes > settings.upload_max_session_bytes:
        raise HTTPException(status_code=413, detail="Upload session exceeds the byte limit")
    if payload.mode is UploadMode.ZIP and (
        len(payload.files) != 1 or not payload.files[0].relative_path.lower().endswith(".zip")
    ):
        raise HTTPException(status_code=422, detail="ZIP mode requires exactly one ZIP file")
    if len({item.relative_path.casefold() for item in payload.files}) != len(payload.files):
        raise HTTPException(status_code=422, detail="Duplicate relative paths are not allowed")

    accepted_bytes = int(
        await session.scalar(
            select(func.coalesce(func.sum(BandAsset.size_bytes), 0)).where(
                BandAsset.dataset_id == dataset.id,
                BandAsset.owner_id == user.id,
                BandAsset.state == "accepted",
            )
        )
        or 0
    )
    reserved_bytes = int(
        await session.scalar(
            select(func.coalesce(func.sum(UploadSession.total_bytes), 0)).where(
                UploadSession.dataset_id == dataset.id,
                UploadSession.__table__.c.owner_id == user.id,
                UploadSession.state.in_({UploadSessionState.PENDING, UploadSessionState.UPLOADING}),
            )
        )
        or 0
    )
    if accepted_bytes + reserved_bytes + total_bytes > settings.upload_max_dataset_bytes:
        raise HTTPException(status_code=413, detail="Dataset storage quota would be exceeded")

    upload_session = UploadSession(
        project_id=dataset.project_id,
        owner_id=user.id,
        dataset_id=dataset.id,
        mode=payload.mode,
        state=UploadSessionState.UPLOADING,
        total_files=len(payload.files),
        total_bytes=total_bytes,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.upload_session_expiry_seconds),
    )
    session.add(upload_session)
    await session.flush()

    objects: list[UploadObject] = []
    for item in payload.files:
        extension, role = classify_extension(item.relative_path)
        upload_object = UploadObject(
            id=uuid4(),
            upload_session_id=upload_session.id,
            project_id=dataset.project_id,
            owner_id=user.id,
            dataset_id=dataset.id,
            client_relative_path=item.relative_path,
            filename=sanitize_filename(item.relative_path),
            extension=extension,
            declared_size=item.size_bytes,
            declared_mime=item.content_type,
            role=role or AssetRole.SCIENTIFIC,
            client_checksum=item.sha256.lower() if item.sha256 else None,
            state=UploadObjectState.PENDING,
            metadata_json={},
            upload_attempts=0,
        )
        session.add(upload_object)
        if item.size_bytes > settings.upload_max_file_bytes:
            mark_invalid(upload_object, "file_too_large", "File exceeds the configured limit")
        elif role is None:
            mark_invalid(
                upload_object, "invalid_extension", "Accepted extensions: TIFF, ZIP, PNG, JPEG"
            )
        elif payload.mode is not UploadMode.ZIP and role.value == "archive":
            mark_invalid(upload_object, "mode_mismatch", "ZIP files require ZIP upload mode")
        elif payload.mode is UploadMode.ZIP and role.value != "archive":
            mark_invalid(upload_object, "mode_mismatch", "ZIP mode accepts only a ZIP archive")
        else:
            upload_object.object_key = quarantine_key(
                upload_session.id, upload_object.id, upload_object.extension
            )
        objects.append(upload_object)

    declared_checksums = {obj.client_checksum for obj in objects if obj.client_checksum}
    if declared_checksums:
        duplicates = set(
            await session.scalars(
                select(BandAsset.sha256).where(
                    BandAsset.dataset_id == dataset.id,
                    BandAsset.sha256.in_(declared_checksums),
                )
            )
        )
        for upload_object in objects:
            if upload_object.client_checksum in duplicates:
                mark_invalid(upload_object, "duplicate", "An identical original already exists")

    storage = storage_for(request, settings)
    plans: dict[UUID, UploadPlanData] = {}
    for upload_object in objects:
        if upload_object.state is not UploadObjectState.PENDING:
            continue
        try:
            plans[upload_object.id] = await storage.initialize(upload_object)
            upload_object.state = UploadObjectState.UPLOADING
            upload_object.upload_attempts += 1
        except Exception as error:
            upload_object.state = UploadObjectState.FAILED
            upload_object.failure_code = "storage_initialization_failed"
            upload_object.failure_message = type(error).__name__

    has_transfer = any(item.state is UploadObjectState.UPLOADING for item in objects)
    if has_transfer:
        dataset.state = DatasetState.UPLOADING
    else:
        upload_session.state = UploadSessionState.COMPLETED
        dataset.state = DatasetState.INVALID
    response_objects = [plan_response(item, plans.get(item.id)) for item in objects]
    await session.commit()
    await session.refresh(upload_session)
    return UploadSessionCreated(
        id=upload_session.id,
        dataset_id=upload_session.dataset_id,
        mode=upload_session.mode,
        state=upload_session.state,
        total_files=upload_session.total_files,
        total_bytes=upload_session.total_bytes,
        expires_at=upload_session.expires_at,
        created_at=upload_session.created_at,
        updated_at=upload_session.updated_at,
        objects=response_objects,
    )


@router.get("/datasets/{dataset_id}/uploads", response_model=list[UploadSessionResponse])
async def list_upload_sessions(
    dataset_id: UUID, session: Session, user: CurrentUser
) -> list[UploadSessionResponse]:
    await get_owned_dataset(session, dataset_id, user.id)
    uploads = list(
        await session.scalars(
            select(UploadSession)
            .where(
                UploadSession.dataset_id == dataset_id,
                UploadSession.__table__.c.owner_id == user.id,
            )
            .order_by(UploadSession.created_at.desc())
        )
    )
    responses: list[UploadSessionResponse] = []
    for upload in uploads:
        objects = list(
            await session.scalars(
                select(UploadObject)
                .where(
                    UploadObject.upload_session_id == upload.id,
                    UploadObject.owner_id == user.id,
                )
                .order_by(UploadObject.created_at)
            )
        )
        responses.append(
            UploadSessionResponse(
                **UploadSessionResponse.model_validate(upload).model_dump(exclude={"objects"}),
                objects=[UploadObjectResponse.model_validate(item) for item in objects],
            )
        )
    return responses


async def _set_desktop_removed(
    upload_session_id: UUID,
    object_id: UUID,
    session: AsyncSession,
    user: Any,
    *,
    removed: bool,
) -> UploadObjectResponse:
    _require_desktop_mode()
    upload = await get_owned_upload_session(session, upload_session_id, user.id)
    upload_object = await get_owned_upload_object(session, upload_session_id, object_id, user.id)
    if upload_object.role is AssetRole.ARCHIVE:
        raise HTTPException(status_code=422, detail="Remove extracted images individually")
    if upload_object.state not in {
        UploadObjectState.VALID,
        UploadObjectState.INVALID,
        UploadObjectState.FAILED,
    }:
        raise HTTPException(status_code=409, detail="Wait for validation or cancel the upload")
    dataset = await get_owned_dataset(session, upload.dataset_id, user.id)
    if dataset.state is DatasetState.ARCHIVED:
        raise HTTPException(status_code=409, detail="Archived dataset cannot be changed")
    metadata = dict(upload_object.metadata_json or {})
    currently_removed = bool(metadata.get("desktop_removed_at"))
    if currently_removed == removed:
        return UploadObjectResponse.model_validate(upload_object)
    asset = await session.scalar(
        select(BandAsset).where(
            BandAsset.upload_object_id == object_id,
            BandAsset.owner_id == user.id,
            BandAsset.dataset_id == upload.dataset_id,
        )
    )
    if asset is not None:
        expected = BandAssetState.ACCEPTED if removed else BandAssetState.ARCHIVED
        if asset.state is not expected:
            raise HTTPException(status_code=409, detail="Image state changed; refresh the list")
        if not removed and asset.capture_id and asset.band_code:
            replacement = await session.scalar(
                select(BandAsset.id)
                .where(
                    BandAsset.capture_id == asset.capture_id,
                    BandAsset.band_code == asset.band_code,
                    BandAsset.state == BandAssetState.ACCEPTED,
                    BandAsset.id != asset.id,
                )
                .limit(1)
            )
            if replacement is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Remove the replacement band before restoring this image",
                )
        asset.state = BandAssetState.ARCHIVED if removed else BandAssetState.ACCEPTED
    if removed:
        metadata["desktop_removed_at"] = datetime.now(UTC).isoformat()
    else:
        metadata.pop("desktop_removed_at", None)
    upload_object.metadata_json = metadata
    active_upload = await session.scalar(
        select(UploadObject.id)
        .where(
            UploadObject.dataset_id == upload.dataset_id,
            UploadObject.owner_id == user.id,
            UploadObject.state.in_(
                {
                    UploadObjectState.PENDING,
                    UploadObjectState.UPLOADING,
                    UploadObjectState.UPLOADED,
                    UploadObjectState.VALIDATING,
                }
            ),
        )
        .limit(1)
    )
    accepted = await session.scalar(
        select(BandAsset.id)
        .where(
            BandAsset.dataset_id == upload.dataset_id,
            BandAsset.owner_id == user.id,
            BandAsset.state == BandAssetState.ACCEPTED,
        )
        .limit(1)
    )
    dataset.state = (
        DatasetState.UPLOADING
        if active_upload is not None
        else DatasetState.NEEDS_REVIEW
        if accepted is not None
        else DatasetState.DRAFT
    )
    await session.commit()
    await session.refresh(upload_object)
    return UploadObjectResponse.model_validate(upload_object)


@router.post(
    "/uploads/{upload_session_id}/objects/{object_id}/archive",
    response_model=UploadObjectResponse,
)
async def archive_desktop_image(
    upload_session_id: UUID, object_id: UUID, session: Session, user: CurrentUser
) -> UploadObjectResponse:
    return await _set_desktop_removed(upload_session_id, object_id, session, user, removed=True)


@router.post(
    "/uploads/{upload_session_id}/objects/{object_id}/restore",
    response_model=UploadObjectResponse,
)
async def restore_desktop_image(
    upload_session_id: UUID, object_id: UUID, session: Session, user: CurrentUser
) -> UploadObjectResponse:
    return await _set_desktop_removed(upload_session_id, object_id, session, user, removed=False)


@router.post(
    "/uploads/{upload_session_id}/objects/{object_id}/parts",
    response_model=UploadPartUrls,
)
async def presign_upload_parts(
    upload_session_id: UUID,
    object_id: UUID,
    payload: UploadPartRequest,
    request: Request,
    session: Session,
    user: CurrentUser,
) -> UploadPartUrls:
    settings = get_settings()
    upload_object = await get_owned_upload_object(session, upload_session_id, object_id, user.id)
    if upload_object.state is not UploadObjectState.UPLOADING:
        raise HTTPException(status_code=409, detail="Upload object is not accepting parts")
    try:
        urls = await storage_for(request, settings).presign_parts(
            upload_object, payload.part_numbers
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return UploadPartUrls(
        object_id=object_id,
        expires_in=settings.upload_url_expiry_seconds,
        parts=[UploadPartUrl(part_number=number, upload_url=url) for number, url in urls],
    )


@router.post(
    "/uploads/{upload_session_id}/objects/{object_id}/complete",
    response_model=UploadObjectResponse,
)
async def complete_upload_object(
    upload_session_id: UUID,
    object_id: UUID,
    payload: UploadComplete,
    request: Request,
    session: Session,
    user: CurrentUser,
) -> UploadObject:
    settings = get_settings()
    upload = await get_owned_upload_session(session, upload_session_id, user.id)
    upload_object = await get_owned_upload_object(session, upload_session_id, object_id, user.id)
    if upload.state is UploadSessionState.CANCELLED:
        raise HTTPException(status_code=409, detail="Upload session was cancelled")
    if upload_object.state in {
        UploadObjectState.VALIDATING,
        UploadObjectState.VALID,
        UploadObjectState.INVALID,
    }:
        return upload_object
    if upload_object.state not in {UploadObjectState.UPLOADING, UploadObjectState.UPLOADED}:
        raise HTTPException(status_code=409, detail="Upload object cannot be completed")

    if upload_object.state is UploadObjectState.UPLOADING:
        try:
            head = await storage_for(request, settings).complete(
                upload_object,
                [(part.part_number, part.etag) for part in payload.parts],
            )
        except Exception as error:
            upload_object.state = UploadObjectState.FAILED
            upload_object.failure_code = "upload_interrupted"
            upload_object.failure_message = type(error).__name__
            await session.commit()
            raise HTTPException(status_code=409, detail="Object upload is incomplete") from error
        observed_size = int(head.get("ContentLength", -1))
        if observed_size != upload_object.declared_size:
            upload_object.state = UploadObjectState.FAILED
            upload_object.failure_code = "size_mismatch"
            upload_object.failure_message = "Stored object size differs from the manifest"
            await session.commit()
            raise HTTPException(status_code=409, detail="Stored object size does not match")
        upload_object.observed_size = observed_size
        upload_object.etag = str(head.get("ETag", "")).strip('"') or None
        upload_object.object_version_id = head.get("VersionId")
        upload_object.client_checksum = (
            payload.client_sha256.lower()
            if payload.client_sha256
            else upload_object.client_checksum
        )
        upload_object.state = UploadObjectState.UPLOADED
        upload_object.uploaded_at = datetime.now(UTC)
        await session.commit()

    await _enqueue_validation(request, upload_object.id, settings)
    await session.refresh(upload_object)
    return upload_object


async def _enqueue_validation(request: Request, object_id: UUID, settings: Settings) -> None:
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise RuntimeError("Application resources are not initialized")
    envelope = {
        "schema_version": 1,
        "message_id": str(uuid4()),
        "message_type": "ingestion.validate.requested",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {"upload_object_id": str(object_id)},
    }
    await resources.redis.xadd(settings.redis_stream, {"data": _json(envelope)})


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


@router.post(
    "/uploads/{upload_session_id}/objects/{object_id}/retry",
    response_model=UploadPlan,
)
async def retry_upload_object(
    upload_session_id: UUID,
    object_id: UUID,
    request: Request,
    session: Session,
    user: CurrentUser,
) -> UploadPlan:
    settings = get_settings()
    upload = await get_owned_upload_session(session, upload_session_id, user.id)
    upload_object = await get_owned_upload_object(session, upload_session_id, object_id, user.id)
    if upload.state is UploadSessionState.CANCELLED:
        raise HTTPException(status_code=409, detail="Cancelled sessions cannot be retried")
    if upload_object.state is not UploadObjectState.FAILED:
        raise HTTPException(status_code=409, detail="Only failed transfers can be retried")
    if upload_object.observed_size is not None and upload_object.object_key is not None:
        upload_object.state = UploadObjectState.UPLOADED
        upload_object.failure_code = None
        upload_object.failure_message = None
        upload.state = UploadSessionState.UPLOADING
        await session.commit()
        await _enqueue_validation(request, upload_object.id, settings)
        await session.refresh(upload_object)
        return plan_response(upload_object, None)
    upload_object.failure_code = None
    upload_object.failure_message = None
    upload_object.multipart_upload_id = None
    try:
        plan = await storage_for(request, settings).initialize(upload_object)
    except Exception as error:
        upload_object.failure_code = "storage_initialization_failed"
        upload_object.failure_message = type(error).__name__
        await session.commit()
        raise HTTPException(status_code=503, detail="Could not restart upload") from error
    upload_object.state = UploadObjectState.UPLOADING
    upload_object.upload_attempts += 1
    upload.state = UploadSessionState.UPLOADING
    await session.commit()
    await session.refresh(upload_object)
    return plan_response(upload_object, plan)


@router.post(
    "/uploads/{upload_session_id}/objects/{object_id}/failed",
    response_model=UploadObjectResponse,
)
async def mark_upload_interrupted(
    upload_session_id: UUID,
    object_id: UUID,
    request: Request,
    session: Session,
    user: CurrentUser,
) -> UploadObject:
    settings = get_settings()
    await get_owned_upload_session(session, upload_session_id, user.id)
    upload_object = await get_owned_upload_object(session, upload_session_id, object_id, user.id)
    if upload_object.state is UploadObjectState.UPLOADING:
        with suppress(Exception):
            await storage_for(request, settings).abort(upload_object)
        upload_object.state = UploadObjectState.FAILED
        upload_object.failure_code = "upload_interrupted"
        upload_object.failure_message = "Transfer was interrupted before completion"
        active_count = await session.scalar(
            select(UploadObject.id)
            .where(
                UploadObject.upload_session_id == upload_session_id,
                UploadObject.id != upload_object.id,
                UploadObject.state.in_(
                    {
                        UploadObjectState.PENDING,
                        UploadObjectState.UPLOADING,
                        UploadObjectState.UPLOADED,
                        UploadObjectState.VALIDATING,
                    }
                ),
            )
            .limit(1)
        )
        if active_count is None:
            upload = await get_owned_upload_session(session, upload_session_id, user.id)
            upload.state = UploadSessionState.FAILED
        await session.commit()
        await session.refresh(upload_object)
    return upload_object


@router.post(
    "/uploads/{upload_session_id}/cancel",
    response_model=UploadSessionResponse,
)
async def cancel_upload_session(
    upload_session_id: UUID,
    request: Request,
    session: Session,
    user: CurrentUser,
) -> UploadSessionResponse:
    settings = get_settings()
    upload = await get_owned_upload_session(session, upload_session_id, user.id)
    if upload.state is UploadSessionState.COMPLETED:
        raise HTTPException(status_code=409, detail="Completed uploads cannot be cancelled")
    objects = list(
        await session.scalars(
            select(UploadObject).where(
                UploadObject.upload_session_id == upload.id,
                UploadObject.owner_id == user.id,
            )
        )
    )
    if upload.state is not UploadSessionState.CANCELLED:
        storage = storage_for(request, settings)
        for upload_object in objects:
            if upload_object.state in {UploadObjectState.PENDING, UploadObjectState.UPLOADING}:
                with suppress(Exception):
                    await storage.abort(upload_object)
                upload_object.state = UploadObjectState.FAILED
                upload_object.failure_code = "upload_cancelled"
                upload_object.failure_message = "Upload cancelled by the user"
        upload.state = UploadSessionState.CANCELLED
        accepted = await session.scalar(
            select(BandAsset.id)
            .where(BandAsset.dataset_id == upload.dataset_id, BandAsset.owner_id == user.id)
            .limit(1)
        )
        dataset = await get_owned_dataset(session, upload.dataset_id, user.id)
        dataset.state = DatasetState.NEEDS_REVIEW if accepted is not None else DatasetState.DRAFT
        await session.commit()
        for upload_object in objects:
            await session.refresh(upload_object)
        await session.refresh(upload)
    try:
        return UploadSessionResponse(
            **UploadSessionResponse.model_validate(upload).model_dump(exclude={"objects"}),
            objects=[UploadObjectResponse.model_validate(item) for item in objects],
        )
    except ValidationError as error:
        raise RuntimeError("Could not serialize upload session") from error
