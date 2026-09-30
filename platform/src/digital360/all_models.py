"""Importe tous les modèles ORM, pour qu'Alembic et `alembic check` voient le schéma complet.

Ajouter ici chaque module de modèles créé.
"""

from digital360.core import audit, consent, idempotency, jobs, workflow
from digital360.core.db import Base
from digital360.modules.catalog.infrastructure import models as catalog_models
from digital360.modules.configuration.infrastructure import models as configuration_models
from digital360.modules.diagnostics.infrastructure import models as diagnostic_models
from digital360.modules.identity.infrastructure import models as identity_models
from digital360.modules.organizations.infrastructure import models as organization_models
from digital360.modules.passport.infrastructure import models as passport_models

__all__ = [
    "Base",
    "audit",
    "catalog_models",
    "configuration_models",
    "consent",
    "diagnostic_models",
    "idempotency",
    "identity_models",
    "jobs",
    "organization_models",
    "passport_models",
    "workflow",
]
