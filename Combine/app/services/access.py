"""Who may touch what. The API uses the service key (RLS bypassed), so these
checks mirror the RLS policies in migration 0005 exactly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.errors import Forbidden, NotFound
from app.store.base import Store, one

ActorRole = Literal["officer", "applicant", "admin", "system"]


@dataclass(frozen=True)
class Actor:
    user_id: str | None
    role: ActorRole
    organisation_id: str | None = None

    @property
    def is_staff(self) -> bool:
        return self.role in ("officer", "admin")


SYSTEM = Actor(user_id=None, role="system")


def require_role(actor: Actor, *roles: ActorRole) -> None:
    if actor.role not in roles:
        raise Forbidden(f"This action requires role: {' or '.join(roles)}")


def staff_can_access_program(store: Store, actor: Actor, grant_program_id: str) -> bool:
    if actor.role in ("admin", "system"):
        return True
    if actor.role != "officer":
        return False
    program = one(store.select("grant_programs", eq={"id": grant_program_id}, limit=1))
    return bool(program and program["organisation_id"] == actor.organisation_id)


def application_for_staff(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    if not (actor.is_staff or actor.role == "system"):
        raise Forbidden("Officers only")
    app = one(store.select("applications", eq={"id": application_id}, limit=1))
    # Not found and not permitted look the same, so ids cannot be probed.
    if not app or not staff_can_access_program(store, actor, app["grant_program_id"]):
        raise NotFound("Application not found")
    return app


def application_for_applicant(store: Store, actor: Actor, application_id: str) -> dict[str, Any]:
    require_role(actor, "applicant")
    app = one(store.select("applications", eq={"id": application_id}, limit=1))
    if not app:
        raise NotFound("Application not found")
    applicant = one(store.select("applicants", eq={"id": app["applicant_id"]}, limit=1))
    if not applicant or applicant.get("user_id") != actor.user_id:
        raise NotFound("Application not found")
    return app
