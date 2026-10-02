from app.schemas.tickets import CustomerTicket, StaffTicket

CUSTOMER_ALLOWLIST = {"id", "title", "description", "status", "created_at", "updated_at"}


def test_customer_ticket_schema_contains_exactly_the_allowlisted_fields() -> None:
    assert set(CustomerTicket.model_fields) == CUSTOMER_ALLOWLIST


def test_staff_ticket_is_a_superset_and_adds_no_database_internals() -> None:
    staff = set(StaffTicket.model_fields)

    assert staff > CUSTOMER_ALLOWLIST
    assert "search_vector" not in staff
    assert "updated_by" not in staff
