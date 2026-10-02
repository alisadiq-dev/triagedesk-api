"""Findings from the Phase 3 RBAC security review, each with a test that proves the fix."""

import asyncio

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import Profile, Ticket
from app.models.enums import Role, TicketStatus
from app.repositories.tickets import TicketRepository
from tests.support.factories import make_ticket
from tests.support.world import World

TICKETS = "/api/v1/tickets"
USERS = "/api/v1/users"
DRAFT = "Dear customer, have you tried turning it off and on again?"


# --- the AI draft is for the assignee and admins only ----------------------------------------


async def test_other_agents_do_not_see_the_ai_draft_but_the_assignee_and_admins_do(
    world: World,
) -> None:
    unassigned = await make_ticket(world.session, world.ids["customer"], ai_suggested_reply=DRAFT)
    mine = await make_ticket(
        world.session,
        world.ids["customer"],
        assignee_id=world.ids["agent"],
        ai_suggested_reply=DRAFT,
    )

    def draft(response_json: dict[str, object]) -> object:
        return response_json["ai_suggested_reply"]

    as_other_agent = await world.client.get(
        f"{TICKETS}/{unassigned.id}", headers=world.auth("agent2")
    )
    as_assignee = await world.client.get(f"{TICKETS}/{mine.id}", headers=world.auth("agent"))
    as_admin = await world.client.get(f"{TICKETS}/{unassigned.id}", headers=world.auth("admin"))

    assert draft(as_other_agent.json()) is None
    assert draft(as_assignee.json()) == DRAFT
    assert draft(as_admin.json()) == DRAFT


async def test_the_ai_draft_follows_the_same_rule_in_lists(world: World) -> None:
    await make_ticket(world.session, world.ids["customer"], title="free", ai_suggested_reply=DRAFT)
    await make_ticket(
        world.session, world.ids["customer"], title="held", assignee_id=world.ids["agent"],
        ai_suggested_reply=DRAFT,
    )  # fmt: skip

    agent = (await world.client.get(TICKETS, headers=world.auth("agent"))).json()["items"]
    agent2 = (await world.client.get(TICKETS, headers=world.auth("agent2"))).json()["items"]

    assert {t["title"]: t["ai_suggested_reply"] for t in agent} == {"free": None, "held": DRAFT}
    assert {t["title"]: t["ai_suggested_reply"] for t in agent2} == {"free": None}


# --- races between role changes, claims, assignments and comments ----------------------------


async def test_a_role_change_waits_for_an_in_flight_claim_then_sees_the_ticket(
    world: World, engine: AsyncEngine
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])
    async with AsyncSession(engine) as in_flight:
        # An uncommitted claim: share-locks the agent's profile and assigns the ticket.
        await in_flight.execute(
            select(Profile).where(Profile.id == world.ids["agent"]).with_for_update(read=True)
        )
        await in_flight.execute(
            update(Ticket).where(Ticket.id == ticket.id).values(assignee_id=world.ids["agent"])
        )
        change = asyncio.create_task(
            world.client.patch(
                f"{USERS}/{world.ids['agent']}",
                json={"role": "customer"},
                headers=world.auth("admin"),
            )
        )
        await asyncio.sleep(0.5)
        assert not change.done(), "the role change must wait for the in-flight claim"
        await in_flight.commit()
        response = await change

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "role_change_blocked"


async def test_a_claim_waits_for_an_in_flight_demotion_then_is_refused(
    world: World, engine: AsyncEngine
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"])
    ticket_id = ticket.id
    async with AsyncSession(engine) as in_flight:
        await in_flight.execute(
            select(Profile).where(Profile.id == world.ids["agent"]).with_for_update()
        )
        await in_flight.execute(
            update(Profile).where(Profile.id == world.ids["agent"]).values(role=Role.CUSTOMER)
        )
        claim = asyncio.create_task(
            world.client.post(f"{TICKETS}/{ticket_id}/claim", headers=world.auth("agent"))
        )
        await asyncio.sleep(0.5)
        assert not claim.done(), "the claim must wait for the in-flight role change"
        await in_flight.commit()
        response = await claim

    assert response.status_code == 403
    world.session.expire_all()
    assert (
        await world.session.scalar(select(Ticket.assignee_id).where(Ticket.id == ticket_id))
    ) is None


async def test_a_comment_waits_for_an_in_flight_reassignment_then_is_refused(
    world: World, engine: AsyncEngine
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    async with AsyncSession(engine) as in_flight:
        await in_flight.execute(
            update(Ticket).where(Ticket.id == ticket.id).values(assignee_id=world.ids["agent2"])
        )
        comment = asyncio.create_task(
            world.client.post(
                f"{TICKETS}/{ticket.id}/comments",
                json={"body": "hello"},
                headers=world.auth("agent"),
            )
        )
        await asyncio.sleep(0.5)
        assert not comment.done(), "the comment must wait for the in-flight reassignment"
        await in_flight.commit()
        response = await comment

    assert response.status_code == 403
    assert (
        await world.session.scalar(select(Ticket.first_responded_at).where(Ticket.id == ticket.id))
    ) is None


async def test_reassign_only_succeeds_if_the_assignee_is_still_the_one_that_was_seen(
    world: World,
) -> None:
    ticket = await make_ticket(world.session, world.ids["customer"], assignee_id=world.ids["agent"])
    repository = TicketRepository(world.session)

    stale = await repository.reassign(ticket.id, None, world.ids["agent2"])
    fresh = await repository.reassign(ticket.id, world.ids["agent"], world.ids["agent2"])

    assert (stale, fresh) == (False, True)


async def test_two_admins_demoting_each_other_at_once_leave_exactly_one_admin(world: World) -> None:
    second_admin = await world.session.get(Profile, world.ids["customer2"])
    assert second_admin is not None
    second_admin.role = Role.ADMIN
    await world.session.commit()
    second = world.ids["customer2"]
    # Both requests authenticate as admin; each tries to demote the other.
    first_demotes_second, second_demotes_first = await asyncio.gather(
        world.client.patch(
            f"{USERS}/{second}", json={"role": "agent"}, headers=world.auth("admin")
        ),
        world.client.patch(
            f"{USERS}/{world.ids['admin']}", json={"role": "agent"}, headers=world.auth("customer2")
        ),
    )

    assert sorted([first_demotes_second.status_code, second_demotes_first.status_code]) == [
        200,
        409,
    ]
    world.session.expire_all()
    admins = (
        await world.session.scalars(select(Profile.id).where(Profile.role == Role.ADMIN))
    ).all()
    assert len(admins) == 1


# --- input bounds and error codes ------------------------------------------------------------


async def test_an_absurd_page_number_is_rejected_instead_of_causing_a_server_error(
    world: World,
) -> None:
    too_far = await world.client.get(
        TICKETS, params={"page": 1_000_001}, headers=world.auth("admin")
    )
    at_limit = await world.client.get(
        TICKETS, params={"page": 1_000_000}, headers=world.auth("admin")
    )

    assert too_far.status_code == 422
    assert at_limit.status_code == 200
    assert at_limit.json()["items"] == []


@pytest.mark.parametrize("body", [{"name": None}, {"is_active": None}])
async def test_category_patch_rejects_null_for_required_fields(
    world: World, body: dict[str, None]
) -> None:
    categories = (await world.client.get("/api/v1/categories", headers=world.auth("admin"))).json()

    response = await world.client.patch(
        f"/api/v1/categories/{categories[0]['id']}", json=body, headers=world.auth("admin")
    )

    assert response.status_code == 422


async def test_a_category_description_can_be_cleared(world: World) -> None:
    created = await world.client.post(
        "/api/v1/categories", json={"name": "Temp", "description": "x"}, headers=world.auth("admin")
    )

    response = await world.client.patch(
        f"/api/v1/categories/{created.json()['id']}",
        json={"description": None},
        headers=world.auth("admin"),
    )

    assert response.status_code == 200
    assert response.json()["description"] is None


# --- staff cannot work on their own tickets --------------------------------------------------


async def test_a_promoted_customer_cannot_claim_or_be_assigned_their_own_ticket(
    world: World,
) -> None:
    own = await make_ticket(
        world.session, world.ids["agent"]
    )  # the agent is this ticket's customer

    claim = await world.client.post(f"{TICKETS}/{own.id}/claim", headers=world.auth("agent"))
    assign = await world.client.put(
        f"{TICKETS}/{own.id}/assignee",
        json={"assignee_id": str(world.ids["agent"])},
        headers=world.auth("admin"),
    )

    assert claim.status_code == 403
    assert assign.status_code == 422


async def test_staff_answering_their_own_ticket_does_not_count_as_a_first_response(
    world: World,
) -> None:
    own = await make_ticket(world.session, world.ids["admin"], status=TicketStatus.OPEN)

    response = await world.client.post(
        f"{TICKETS}/{own.id}/comments",
        json={"body": "I answer myself"},
        headers=world.auth("admin"),
    )

    assert response.status_code == 201
    assert (
        await world.session.scalar(select(Ticket.first_responded_at).where(Ticket.id == own.id))
    ) is None
