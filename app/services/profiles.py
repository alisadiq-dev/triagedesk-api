from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AuthenticatedUser
from app.models import Profile
from app.repositories.profiles import ProfileRepository


class ProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._profiles = ProfileRepository(session)

    async def ensure_profile(self, user: AuthenticatedUser) -> Profile:
        """Create the profile on the first valid request (customer); never touch an existing one.

        Existing users cost one SELECT. The insert is still ON CONFLICT DO NOTHING, so two
        concurrent first requests cannot fail or create duplicates.
        """
        profile = await self._profiles.get(user.id)
        if profile is not None:
            return profile
        await self._profiles.insert_if_absent(user.id, user.email)
        await self._session.commit()
        profile = await self._profiles.get(user.id)
        if profile is None:
            raise RuntimeError("profile missing right after insert")
        return profile
