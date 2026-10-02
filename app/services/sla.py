from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SlaPolicy
from app.models.enums import Priority, Role
from app.repositories.config import SlaPolicyRepository
from app.schemas.config import SlaPolicyUpdate
from app.services.errors import InputError, NotFoundError
from app.services.permissions import Actor, require_role


def compute_deadlines(policy: SlaPolicy, created_at: datetime) -> tuple[datetime, datetime]:
    """(first_response_due_at, resolution_due_at). Always counted from created_at, 24/7."""
    return (
        created_at + timedelta(hours=policy.response_hours),
        created_at + timedelta(hours=policy.resolution_hours),
    )


async def policy_for(policies: SlaPolicyRepository, priority: Priority) -> SlaPolicy:
    policy = await policies.get(priority)
    if policy is None:
        raise RuntimeError(f"SLA policy for {priority} is missing; run make seed")
    return policy


class SlaPolicyService:
    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._policies = SlaPolicyRepository(session)

    async def list_policies(self) -> list[SlaPolicy]:
        require_role(self._actor, Role.AGENT, Role.ADMIN)
        return await self._policies.list()

    async def update(self, priority: Priority, data: SlaPolicyUpdate) -> SlaPolicy:
        require_role(self._actor, Role.ADMIN)
        policy = await self._policies.get(priority)
        if policy is None:
            raise NotFoundError("SLA policy not found")
        response = data.response_hours if data.response_hours is not None else policy.response_hours
        resolution = (
            data.resolution_hours if data.resolution_hours is not None else policy.resolution_hours
        )
        if resolution < response:
            raise InputError("resolution_hours must be at least response_hours")
        policy.response_hours = response
        policy.resolution_hours = resolution
        policy.updated_by = self._actor.id
        await self._session.commit()
        await self._session.refresh(policy)
        return policy
