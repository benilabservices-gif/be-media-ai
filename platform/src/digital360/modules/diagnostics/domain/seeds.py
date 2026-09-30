"""Chargement des fichiers de configuration YAML (config/seeds/)."""

from pathlib import Path
from typing import Any

import yaml

from digital360.modules.diagnostics.domain.config import (
    QuestionnaireDefinition,
    RuleSet,
    ScoringModel,
)

SEEDS_DIR = Path("config/seeds")


def _read(path: Path) -> dict[str, Any]:
    # safe_load : aucun objet Python arbitraire ne peut être instancié depuis le fichier
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} : document YAML attendu (objet)")
    return data


def load_questionnaire(path: Path) -> QuestionnaireDefinition:
    return QuestionnaireDefinition.model_validate(_read(path))


def load_scoring_model(path: Path) -> ScoringModel:
    return ScoringModel.model_validate(_read(path))


def load_rule_set(path: Path) -> RuleSet:
    return RuleSet.model_validate(_read(path))
