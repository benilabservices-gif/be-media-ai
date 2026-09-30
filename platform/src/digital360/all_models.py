"""Importe tous les modèles ORM, pour qu'Alembic et `alembic check` voient le schéma complet.

Ajouter ici chaque module de modèles créé (M2+).
"""

from digital360.core import audit, idempotency, jobs, workflow
from digital360.core.db import Base

__all__ = ["Base", "audit", "idempotency", "jobs", "workflow"]
