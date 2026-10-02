import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.security import AuthenticatedUser
from app.models import Profile
from app.models.enums import Role
from app.repositories.profiles import ProfileRepository
from app.services.profiles import ProfileService


async def test_first_request_creates_a_customer_profile(session: AsyncSession) -> None:
    user = AuthenticatedUser(id=uuid.uuid4(), email="ada@example.com")

    profile = await ProfileService(session).ensure_profile(user)

    assert profile.id == user.id
    assert profile.role == Role.CUSTOMER
    assert profile.email == "ada@example.com"


async def test_second_request_reuses_the_same_profile(session: AsyncSession) -> None:
    user = AuthenticatedUser(id=uuid.uuid4(), email=None)
    service = ProfileService(session)

    await service.ensure_profile(user)
    await service.ensure_profile(user)

    count = await session.scalar(select(func.count()).select_from(Profile))
    assert count == 1


async def test_ensure_profile_never_changes_an_existing_role(session: AsyncSession) -> None:
    admin_id = uuid.uuid4()
    session.add(Profile(id=admin_id, role=Role.ADMIN))
    await session.commit()

    profile = await ProfileService(session).ensure_profile(
        AuthenticatedUser(id=admin_id, email=None)
    )

    assert profile.role == Role.ADMIN


async def test_two_concurrent_first_requests_create_exactly_one_profile(
    engine: AsyncEngine,
) -> None:
    user = AuthenticatedUser(id=uuid.uuid4(), email="race@example.com")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def first_request() -> Profile:
        async with factory() as session:
            return await ProfileService(session).ensure_profile(user)

    results = await asyncio.gather(*(first_request() for _ in range(5)))

    assert {profile.id for profile in results} == {user.id}
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Profile)) == 1


async def test_an_existing_profile_is_read_without_any_write(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = AuthenticatedUser(id=uuid.uuid4(), email=None)
    await ProfileService(session).ensure_profile(user)

    async def forbidden_insert(*args: object, **kwargs: object) -> None:
        raise AssertionError("existing users must not trigger an insert")

    monkeypatch.setattr(ProfileRepository, "insert_if_absent", forbidden_insert)

    profile = await ProfileService(session).ensure_profile(user)

    assert profile.id == user.id
