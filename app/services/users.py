import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Profile
from app.models.enums import Role
from app.repositories.profiles import ProfileRepository
from app.repositories.tickets import TicketRepository
from app.schemas.common import PageParams
from app.services.errors import CannotChangeOwnRoleError, NotFoundError, RoleChangeBlockedError
from app.services.permissions import Actor, require_role

audit_log = logging.getLogger("app.audit")


class UserService:
    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self._session = session
        self._actor = actor
        self._profiles = ProfileRepository(session)
        self._tickets = TicketRepository(session)

    async def list_users(self, role: Role | None, page: PageParams) -> tuple[list[Profile], int]:
        require_role(self._actor, Role.ADMIN)
        return await self._profiles.list_page(role, page.offset, page.page_size)

    async def get_user(self, user_id: uuid.UUID) -> Profile:
        require_role(self._actor, Role.ADMIN)
        return await self._get(user_id)

    async def change_role(self, user_id: uuid.UUID, new_role: Role) -> Profile:
        require_role(self._actor, Role.ADMIN)
        # Lock all admins first (fixed order), then the target, so concurrent role changes, claims
        # and assignments cannot slip past the checks below.
        admin_ids = await self._profiles.lock_admins()
        target = await self._profiles.get_locked(user_id, exclusive=True)
        if target is None:
            raise NotFoundError("User not found")
        if target.id == self._actor.id:
            raise CannotChangeOwnRoleError
        old_role = target.role
        if old_role == new_role:
            return target
        if old_role == Role.ADMIN and not any(admin != target.id for admin in admin_ids):
            raise RoleChangeBlockedError("At least one admin must remain")
        if new_role == Role.CUSTOMER and await self._tickets.has_open_assigned_tickets(target.id):
            raise RoleChangeBlockedError
        target.role = new_role
        await self._session.commit()
        audit_log.info(
            "role_changed",
            extra={
                "actor_id": str(self._actor.id),
                "target_id": str(target.id),
                "old_role": old_role.value,
                "new_role": new_role.value,
            },
        )
        return target

    async def _get(self, user_id: uuid.UUID) -> Profile:
        profile = await self._profiles.get(user_id)
        if profile is None:
            raise NotFoundError("User not found")
        return profile
