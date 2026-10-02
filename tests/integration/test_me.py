from tests.support.world import World


async def test_me_returns_the_callers_id_email_and_role(world: World) -> None:
    response = await world.client.get("/api/v1/me", headers=world.auth("agent"))

    assert response.status_code == 200
    assert response.json() == {
        "id": str(world.ids["agent"]),
        "email": "agent@example.com",
        "role": "agent",
    }


async def test_me_requires_authentication(world: World) -> None:
    response = await world.client.get("/api/v1/me")

    assert response.status_code == 401


async def test_every_response_carries_a_request_id(world: World) -> None:
    response = await world.client.get("/api/v1/me", headers=world.auth("admin"))

    assert len(response.headers["x-request-id"]) == 32
