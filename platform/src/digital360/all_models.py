"""Importe tous les modèles ORM, pour qu'Alembic et `alembic check` voient le schéma complet.

Ajouter ici chaque module de modèles créé.
"""

from digital360.core import audit, idempotency, jobs, workflow
from digital360.core.db import Base
from digital360.modules.identity.infrastructure import models as identity_models
from digital360.modules.organizations.infrastructure import models as organization_models

__all__ = [
    "Base",
    "audit",
    "idempotency",
    "identity_models",
    "jobs",
    "organization_models",
    "workflow",
]
