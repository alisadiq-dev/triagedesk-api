import uuid

import pytest

from app.models.enums import Role
from app.services.permissions import Actor, ForbiddenError, require_role


def actor(role: Role) -> Actor:
    return Actor(id=uuid.uuid4(), role=role)


@pytest.mark.parametrize(
    ("role", "allowed", "passes"),
    [
        (Role.CUSTOMER, (Role.CUSTOMER,), True),
        (Role.CUSTOMER, (Role.AGENT, Role.ADMIN), False),
        (Role.AGENT, (Role.AGENT, Role.ADMIN), True),
        (Role.AGENT, (Role.ADMIN,), False),
        (Role.ADMIN, (Role.ADMIN,), True),
        (Role.ADMIN, (Role.CUSTOMER,), False),
    ],
)
def test_require_role_allows_only_the_listed_roles(
    role: Role, allowed: tuple[Role, ...], passes: bool
) -> None:
    if passes:
        require_role(actor(role), *allowed)
    else:
        with pytest.raises(ForbiddenError):
            require_role(actor(role), *allowed)
