"""Request-scoped dependencies shared by API routers."""

from collections.abc import AsyncIterator
from hashlib import sha256
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from spectral_common import get_settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spectral_api.auth import InvalidAccessTokenError, validate_access_token
from spectral_api.domain.models import User

DEVELOPMENT_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a database session created by the application runtime."""
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "session_factory", None
    )
    if factory is None:
        resources = getattr(request.app.state, "resources", None)
        factory = getattr(resources, "session_factory", None)
    if factory is None:
        raise RuntimeError("Database session factory is not initialized")

    async with factory() as session:
        yield session


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_dev_user_id: Annotated[UUID | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve a local development identity or a validated Auth0 JWT subject."""
    settings = get_settings()
    if settings.development_auth_enabled:
        user_id = x_dev_user_id or DEVELOPMENT_USER_ID
        user = await session.get(User, user_id)
        if user is None:
            user = User(
                id=user_id,
                email=f"developer-{user_id}@local.invalid",
                display_name="Development Researcher",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = await validate_access_token(token.strip(), settings)
    except InvalidAccessTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    subject = str(claims["sub"])
    user = await session.scalar(select(User).where(User.auth_subject == subject))
    if user is None:
        raw_email = claims.get("email")
        email = raw_email.strip().lower() if isinstance(raw_email, str) else ""
        verified = claims.get("email_verified") is True
        existing = await session.scalar(select(User).where(User.email == email)) if email else None
        if existing is not None:
            if not verified or existing.auth_subject not in {None, subject}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The email is already linked to another identity",
                )
            existing.auth_subject = subject
            user = existing
        if user is None:
            if not email:
                identity_hash = sha256(subject.encode("utf-8")).hexdigest()[:24]
                email = f"auth0-{identity_hash}@identity.local.invalid"
            display_name_claim = claims.get("name") or claims.get("nickname")
            display_name = (
                display_name_claim.strip()
                if isinstance(display_name_claim, str) and display_name_claim.strip()
                else email.split("@", maxsplit=1)[0]
            )
            user = User(auth_subject=subject, email=email, display_name=display_name[:200])
            session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
