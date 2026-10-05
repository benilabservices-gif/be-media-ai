"""Indicateurs suivis chaque mois et étapes de la mise en place de Google Business.

Saisis par l'équipe (étape 1) depuis les tableaux de bord de Google et de l'outil de mesure
du site ; plus tard récupérés automatiquement, avec les mêmes clés.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from digital360.core.errors import AppError


class Channel(StrEnum):
    GOOGLE_BUSINESS = "GOOGLE_BUSINESS"
    WEBSITE = "WEBSITE"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    # count : nombre entier ; rating : note de 1 à 5
    kind: str = "count"
    # False : une baisse est une bonne nouvelle (avis sans réponse)
    higher_is_better: bool = True


METRICS: dict[Channel, tuple[Metric, ...]] = {
    Channel.GOOGLE_BUSINESS: (
        Metric("views_search", "Vues sur Google Recherche"),
        Metric("views_maps", "Vues sur Google Maps"),
        Metric("calls", "Appels"),
        Metric("website_clicks", "Clics vers le site"),
        Metric("directions", "Demandes d'itinéraire"),
        Metric("messages", "Messages et conversations"),
        Metric("reviews_total", "Avis au total"),
        Metric("rating_average", "Note moyenne", kind="rating"),
        Metric("reviews_unanswered", "Avis sans réponse", higher_is_better=False),
    ),
    Channel.WEBSITE: (
        Metric("visitors", "Visiteurs"),
        Metric("page_views", "Pages vues"),
        Metric("from_google", "Visiteurs venus de Google"),
        Metric("from_social", "Visiteurs venus des réseaux sociaux"),
        Metric("from_whatsapp", "Visiteurs venus de WhatsApp"),
        Metric("direct", "Visiteurs en accès direct"),
        Metric("whatsapp_clicks", "Clics sur WhatsApp"),
        Metric("call_clicks", "Clics sur « Appeler »"),
    ),
}

MAX_COUNT = 1_000_000_000
MAX_TOP_SEARCHES = 5


class SetupStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PROFILE_CREATED = "PROFILE_CREATED"
    ACCESS_GRANTED = "ACCESS_GRANTED"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"


# Dans l'ordre : chaque étape franchie valide les précédentes
SETUP_STEPS: tuple[tuple[SetupStatus, str], ...] = (
    (SetupStatus.PROFILE_CREATED, "Fiche créée"),
    (SetupStatus.ACCESS_GRANTED, "Accès donné à BENILAB"),
    (SetupStatus.VERIFIED, "Fiche validée par Google"),
    (SetupStatus.ACTIVE, "Fiche active, gérée par BENILAB"),
)


def setup_steps(status: SetupStatus) -> list[dict[str, Any]]:
    reached = [step for step, _ in SETUP_STEPS].index(status) if status in dict(SETUP_STEPS) else -1
    return [
        {"key": step.value, "label": label, "done": index <= reached}
        for index, (step, label) in enumerate(SETUP_STEPS)
    ]


def _invalid(field: str, detail: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        detail,
        status=422,
        errors=[{"field": f"metrics.{field}", "reason": "invalid"}],
    )


def clean_metrics(channel: Channel, metrics: dict[str, Any]) -> dict[str, int | float]:
    """Garde les indicateurs connus du canal ; un indicateur vide n'est pas mesuré ce mois-ci."""
    known = {metric.key: metric for metric in METRICS[channel]}
    cleaned: dict[str, int | float] = {}
    for key, value in metrics.items():
        metric = known.get(key)
        if metric is None:
            raise _invalid(key, f"Indicateur inconnu pour ce canal : {key}.")
        if value is None or value == "":
            continue
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _invalid(key, f"{metric.label} : un nombre est attendu.")
        if metric.kind == "rating":
            if not 1 <= value <= 5:
                raise _invalid(key, f"{metric.label} : entre 1 et 5.")
            cleaned[key] = round(float(value), 1)
        else:
            if value < 0 or value > MAX_COUNT or int(value) != value:
                raise _invalid(key, f"{metric.label} : un nombre entier positif est attendu.")
            cleaned[key] = int(value)
    return cleaned


def changes(
    channel: Channel, current: dict[str, Any], previous: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """Évolution par rapport au mois précédent : % pour un nombre, écart de points pour une note.

    `good` dit si l'évolution est une bonne nouvelle (une baisse des avis sans réponse l'est).
    """
    result: dict[str, dict[str, Any]] = {}
    if not previous:
        return result
    for metric in METRICS[channel]:
        now, before = current.get(metric.key), previous.get(metric.key)
        if now is None or before is None:
            continue
        if metric.kind == "rating":
            delta = round(now - before, 1)
            result[metric.key] = {"points": delta, "good": None if delta == 0 else delta > 0}
        elif before > 0:
            percent = round((now - before) / before * 100)
            good = None if percent == 0 else (percent > 0) == metric.higher_is_better
            result[metric.key] = {"percent": percent, "good": good}
    return result


def definitions(channel: Channel) -> list[dict[str, str]]:
    return [
        {"key": metric.key, "label": metric.label, "kind": metric.kind}
        for metric in METRICS[channel]
    ]
