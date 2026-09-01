"""Request-scoped dependencies shared by API routers."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from spectral_common import get_settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
) -> User:
    """Resolve a development identity until the authentication provider is integrated.

    Production disables this bridge by default. The header is deliberately only an
    identity selector in development and test environments; authorization remains
    enforced through owner-scoped queries in every router.
    """
    settings = get_settings()
    if not settings.development_auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is not configured for this environment",
        )

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


CurrentUser = Annotated[User, Depends(get_current_user)]
