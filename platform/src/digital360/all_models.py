"""Importe tous les modèles ORM, pour qu'Alembic et `alembic check` voient le schéma complet.

Ajouter ici chaque module de modèles créé.
"""

from digital360.core import audit, consent, idempotency, jobs, workflow
from digital360.core.db import Base
from digital360.modules.billing.infrastructure import models as billing_models
from digital360.modules.catalog.infrastructure import models as catalog_models
from digital360.modules.configuration.infrastructure import models as configuration_models
from digital360.modules.diagnostics.infrastructure import models as diagnostic_models
from digital360.modules.identity.infrastructure import models as identity_models
from digital360.modules.organizations.infrastructure import models as organization_models
from digital360.modules.passport.infrastructure import models as passport_models
from digital360.modules.performance.infrastructure import models as performance_models
from digital360.modules.projects.infrastructure import models as project_models
from digital360.modules.referrals.infrastructure import models as referral_models

__all__ = [
    "Base",
    "audit",
    "billing_models",
    "catalog_models",
    "configuration_models",
    "consent",
    "diagnostic_models",
    "idempotency",
    "identity_models",
    "jobs",
    "organization_models",
    "passport_models",
    "performance_models",
    "project_models",
    "referral_models",
    "workflow",
]
