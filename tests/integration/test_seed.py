import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Profile, SlaPolicy
from app.models.enums import Priority, Role
from app.seed import seed

ADMIN_SUB = uuid.UUID("11111111-2222-3333-4444-555555555555")


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_seed_creates_the_default_categories_and_sla_policies(session: AsyncSession) -> None:
    await seed(session, bootstrap_admin_sub=None)

    names = set((await session.scalars(select(Category.name))).all())
    policies = {
        p.priority: (p.response_hours, p.resolution_hours)
        for p in (await session.scalars(select(SlaPolicy))).all()
    }
    assert names == {
        "Billing",
        "Technical Issue",
        "Account Access",
        "Feature Request",
        "General Inquiry",
    }
    assert policies == {
        Priority.URGENT: (1, 4),
        Priority.HIGH: (4, 24),
        Priority.MEDIUM: (8, 72),
        Priority.LOW: (24, 168),
    }


async def test_seed_is_idempotent(session: AsyncSession) -> None:
    await seed(session, bootstrap_admin_sub=ADMIN_SUB)
    await seed(session, bootstrap_admin_sub=ADMIN_SUB)

    assert await count(session, Category) == 5
    assert await count(session, SlaPolicy) == 4
    assert await count(session, Profile) == 1


async def test_seed_never_overwrites_edits_made_by_an_admin(session: AsyncSession) -> None:
    await seed(session, bootstrap_admin_sub=None)
    billing = await session.scalar(select(Category).where(Category.name == "Billing"))
    assert billing is not None
    billing.is_active = False
    billing.description = "edited by admin"
    urgent = await session.get(SlaPolicy, Priority.URGENT)
    assert urgent is not None
    urgent.response_hours = 2
    await session.commit()

    await seed(session, bootstrap_admin_sub=None)

    await session.refresh(billing)
    await session.refresh(urgent)
    assert billing.is_active is False
    assert billing.description == "edited by admin"
    assert urgent.response_hours == 2


async def test_seed_creates_the_bootstrap_admin_from_the_supabase_sub(
    session: AsyncSession,
) -> None:
    await seed(session, bootstrap_admin_sub=ADMIN_SUB)

    admin = await session.get(Profile, ADMIN_SUB)
    assert admin is not None
    assert admin.role == Role.ADMIN


async def test_seed_promotes_an_existing_profile_to_admin(session: AsyncSession) -> None:
    session.add(Profile(id=ADMIN_SUB, role=Role.CUSTOMER, email="boss@example.com"))
    await session.commit()

    await seed(session, bootstrap_admin_sub=ADMIN_SUB)

    session.expire_all()
    admin = await session.get(Profile, ADMIN_SUB)
    assert admin is not None
    assert admin.role == Role.ADMIN
    assert admin.email == "boss@example.com"


async def test_seed_without_a_bootstrap_sub_creates_no_profiles(session: AsyncSession) -> None:
    await seed(session, bootstrap_admin_sub=None)

    assert await count(session, Profile) == 0
