"""Commandes d'administration : `python -m digital360.cli <commande>`.

grant-staff-role --email admin@benilab.ci --role ADMIN
export-openapi   (régénère contracts/openapi.json)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from digital360.core.actor import Actor
from digital360.core.audit import record_audit
from digital360.core.config import get_settings
from digital360.core.db import create_engine, create_session_factory
from digital360.core.permissions import StaffRole
from digital360.core.tenancy import staff_transaction
from digital360.modules.identity.infrastructure.models import StaffRoleAssignment, User

OPENAPI_PATH = Path("contracts/openapi.json")


async def grant_staff_role(email: str, role: StaffRole) -> str:
    engine = create_engine(str(get_settings().database_url))
    try:
        async with staff_transaction(create_session_factory(engine)) as session:
            user = (
                await session.execute(select(User).where(func.lower(User.email) == email.lower()))
            ).scalar_one_or_none()
            if user is None:
                return f"Aucun utilisateur avec l'email {email} : il doit d'abord créer son compte."
            await session.execute(
                insert(StaffRoleAssignment)
                .values(user_id=user.id, role=role.value)
                .on_conflict_do_nothing()
            )
            await record_audit(
                session,
                actor=Actor.system("cli"),
                action="staff_role.grant",
                entity_type="user",
                entity_id=user.id,
                new_value={"role": role.value},
            )
        return f"Rôle {role.value} attribué à {email}."
    finally:
        await engine.dispose()


def openapi_document() -> str:
    """Contrat publié pour le frontend, sérialisé de façon stable (diff lisible en revue)."""
    from digital360.main import create_app

    return json.dumps(create_app().openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m digital360.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    grant = commands.add_parser("grant-staff-role", help="attribuer un rôle staff BENILAB")
    grant.add_argument("--email", required=True)
    grant.add_argument("--role", required=True, choices=[role.value for role in StaffRole])

    commands.add_parser("export-openapi", help="régénérer contracts/openapi.json")

    args = parser.parse_args(argv)
    if args.command == "grant-staff-role":
        print(asyncio.run(grant_staff_role(args.email, StaffRole(args.role))))
    elif args.command == "export-openapi":
        OPENAPI_PATH.parent.mkdir(exist_ok=True)
        OPENAPI_PATH.write_text(openapi_document(), encoding="utf-8")
        print(f"{OPENAPI_PATH} mis à jour.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
