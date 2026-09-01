"""Researcher-owned band sequence configuration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import BandSequenceProfile
from spectral_api.routes.datasets import get_owned_dataset
from spectral_api.schemas.band_sequences import (
    BandSequenceProfileCreate,
    BandSequenceProfileResponse,
    DatasetBandSequenceProfileUpdate,
)

router = APIRouter(tags=["band-sequences"])
Session = Annotated[AsyncSession, Depends(get_session)]
DJI_PROFILE = {
    "name": "DJI Multispectral (1–5)",
    "filename_prefix": "DJI_",
    "capture_prefix": "DJI_",
    "band_order": ["BLUE", "GREEN", "RED", "RED_EDGE", "NIR"],
}


async def ensure_dji_profile(session: AsyncSession, owner_id: UUID) -> BandSequenceProfile:
    profile = await session.scalar(
        select(BandSequenceProfile).where(
            BandSequenceProfile.owner_id == owner_id,
            BandSequenceProfile.name == DJI_PROFILE["name"],
        )
    )
    if profile is None:
        profile = BandSequenceProfile(owner_id=owner_id, **DJI_PROFILE)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    return profile


@router.get("/band-sequence-profiles", response_model=list[BandSequenceProfileResponse])
async def list_profiles(session: Session, user: CurrentUser) -> list[BandSequenceProfile]:
    await ensure_dji_profile(session, user.id)
    profiles = await session.scalars(
        select(BandSequenceProfile)
        .where(BandSequenceProfile.owner_id == user.id)
        .order_by(BandSequenceProfile.created_at)
    )
    return list(profiles)


@router.post(
    "/band-sequence-profiles",
    response_model=BandSequenceProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    payload: BandSequenceProfileCreate, session: Session, user: CurrentUser
) -> BandSequenceProfile:
    profile = BandSequenceProfile(owner_id=user.id, **payload.model_dump())
    session.add(profile)
    try:
        await session.commit()
    except Exception as error:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A band sequence with this name already exists"
        ) from error
    await session.refresh(profile)
    return profile


@router.put(
    "/datasets/{dataset_id}/band-sequence-profile", response_model=BandSequenceProfileResponse
)
async def assign_profile(
    dataset_id: UUID,
    payload: DatasetBandSequenceProfileUpdate,
    session: Session,
    user: CurrentUser,
) -> BandSequenceProfile:
    dataset = await get_owned_dataset(session, dataset_id, user.id)
    profile = await session.scalar(
        select(BandSequenceProfile).where(
            BandSequenceProfile.id == payload.profile_id,
            BandSequenceProfile.owner_id == user.id,
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Band sequence profile not found")
    dataset.band_sequence_profile_id = profile.id
    await session.commit()
    return profile
