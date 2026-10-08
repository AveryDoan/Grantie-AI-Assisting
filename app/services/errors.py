"""Service errors. The API maps them to HTTP status codes."""

from __future__ import annotations


class ServiceError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(ServiceError):
    status_code = 404
    code = "not_found"


class Forbidden(ServiceError):
    status_code = 403
    code = "forbidden"


class Conflict(ServiceError):
    status_code = 409
    code = "conflict"


class ValidationFailed(ServiceError):
    status_code = 422
    code = "validation_failed"


class ManualAssessmentRequested(Conflict):
    code = "manual_assessment_requested"


class SignOffBlocked(Conflict):
    code = "signoff_blocked"
