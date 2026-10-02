from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.main import create_app


class InvalidTransition(AppError):
    status_code = 409
    code = "invalid_transition"


def build_client() -> TestClient:
    app: FastAPI = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("database password is hunter2")

    @app.get("/conflict")
    async def conflict() -> None:
        raise InvalidTransition("Cannot go from open to closed")

    @app.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    return TestClient(app, raise_server_exceptions=False)


def test_unknown_route_uses_the_error_format() -> None:
    response = build_client().get("/nope")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_wrong_method_uses_the_error_format() -> None:
    response = build_client().post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_app_error_maps_to_its_status_code_and_code() -> None:
    response = build_client().get("/conflict")

    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "invalid_transition", "message": "Cannot go from open to closed"}
    }


def test_validation_error_returns_422_without_echoing_the_input() -> None:
    response = build_client().get("/items/not-a-number")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "item_id" in body["error"]["message"]
    assert "not-a-number" not in response.text


def test_unhandled_exception_returns_generic_500_without_leaking_details() -> None:
    response = build_client().get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "hunter2" not in response.text


def test_malformed_json_is_400_not_422() -> None:
    from pydantic import BaseModel

    class Payload(BaseModel):
        name: str

    app = create_app()

    @app.post("/payload")
    async def payload(body: Payload) -> dict[str, str]:
        return {"name": body.name}

    client = TestClient(app)

    malformed = client.post(
        "/payload", content=b"{not json", headers={"content-type": "application/json"}
    )
    invalid = client.post("/payload", json={"name": 5})

    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "bad_request"
    assert invalid.status_code == 422
