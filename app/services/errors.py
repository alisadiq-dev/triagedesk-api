"""Errors raised by services; the API turns them into the shared error format."""

from app.core.errors import AppError


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message)


class InputError(AppError):
    """422: a well-formed request whose value is not acceptable (e.g. an inactive category)."""

    status_code = 422
    code = "validation_error"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class TicketClosedError(ConflictError):
    code = "ticket_closed"

    def __init__(self) -> None:
        super().__init__("The ticket is closed and can no longer be changed")


class AlreadyAssignedError(ConflictError):
    code = "already_assigned"

    def __init__(self) -> None:
        super().__init__("The ticket is already assigned")


class NameTakenError(ConflictError):
    code = "name_taken"

    def __init__(self) -> None:
        super().__init__("A category with this name already exists")


class InvalidTransitionError(ConflictError):
    code = "invalid_transition"


class RoleChangeBlockedError(ConflictError):
    code = "role_change_blocked"

    def __init__(self, message: str = "Reassign or close this user's open tickets first") -> None:
        super().__init__(message)


class CannotChangeOwnRoleError(ConflictError):
    code = "cannot_change_own_role"

    def __init__(self) -> None:
        super().__init__("You cannot change your own role")
