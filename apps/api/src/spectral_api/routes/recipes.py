"""Owner-scoped immutable processing recipe endpoints."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from spectral_common import RECIPE_SCHEMA_VERSION, get_formula_definition
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from spectral_api.dependencies import CurrentUser, get_session
from spectral_api.domain.models import ProcessingRecipe
from spectral_api.schemas.recipes import (
    ProcessingRecipeCreate,
    ProcessingRecipeResponse,
    RecipeIndexResponse,
)

router = APIRouter(tags=["processing-recipes"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _checksum(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _response(recipe: ProcessingRecipe) -> ProcessingRecipeResponse:
    raw_indices = recipe.definition_snapshot.get("indices", [])
    raw_statistics = recipe.definition_snapshot.get("statistics", [])
    indices = (
        [RecipeIndexResponse.model_validate(item) for item in raw_indices]
        if isinstance(raw_indices, list)
        else []
    )
    return ProcessingRecipeResponse(
        id=recipe.id,
        name=recipe.name,
        description=recipe.description,
        schema_version=recipe.schema_version,
        definition_checksum=recipe.definition_checksum,
        indices=indices,
        statistics=[str(value) for value in raw_statistics]
        if isinstance(raw_statistics, list)
        else [],
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
    )


@router.post(
    "/processing-recipes",
    response_model=ProcessingRecipeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_recipe(
    payload: ProcessingRecipeCreate, session: Session, user: CurrentUser
) -> ProcessingRecipeResponse:
    indices: list[dict[str, Any]] = []
    for requested in payload.indices:
        definition = get_formula_definition(requested.name)
        parameters = definition.parameters_with_defaults(dict(requested.parameters))
        indices.append(
            {
                "index": definition.index,
                "formula_version": definition.formula_version,
                "formula": definition.expression,
                "required_bands": list(definition.required_bands),
                "parameters": parameters,
                "scientific_note": definition.scientific_note,
            }
        )
    snapshot: dict[str, Any] = {
        "schema_version": RECIPE_SCHEMA_VERSION,
        "indices": indices,
        "statistics": list(payload.statistics),
    }
    recipe = ProcessingRecipe(
        id=uuid4(),
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        definition_snapshot=snapshot,
        definition_checksum=_checksum(snapshot),
        schema_version=RECIPE_SCHEMA_VERSION,
    )
    session.add(recipe)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="A recipe with this name already exists"
        ) from error
    await session.refresh(recipe)
    return _response(recipe)


@router.get("/processing-recipes", response_model=list[ProcessingRecipeResponse])
async def list_recipes(session: Session, user: CurrentUser) -> list[ProcessingRecipeResponse]:
    recipes = list(
        await session.scalars(
            select(ProcessingRecipe)
            .where(ProcessingRecipe.owner_id == user.id, ProcessingRecipe.archived_at.is_(None))
            .order_by(ProcessingRecipe.name)
        )
    )
    return [_response(recipe) for recipe in recipes]


@router.get("/processing-recipes/{recipe_id}", response_model=ProcessingRecipeResponse)
async def get_recipe(
    recipe_id: UUID, session: Session, user: CurrentUser
) -> ProcessingRecipeResponse:
    recipe = await session.scalar(
        select(ProcessingRecipe).where(
            ProcessingRecipe.id == recipe_id,
            ProcessingRecipe.owner_id == user.id,
            ProcessingRecipe.archived_at.is_(None),
        )
    )
    if recipe is None:
        raise HTTPException(status_code=404, detail="Processing recipe not found")
    return _response(recipe)
