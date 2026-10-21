"""Digital Start : offre encadrée, conçue avec l'IA à partir de modèles.

Le client ne commande pas un site sur mesure. La conception est offerte ; il paie le nom de
domaine, l'hébergement et la mise en ligne. Le brief est donc un formulaire guidé, borné ici,
et le serveur refuse tout ce qui sort du cadre (les corrections portent sur le contenu seul).
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints

PRODUCT_CODE = "DIGITAL_START"
DELIVERY_BUSINESS_DAYS = 7
MAX_REVISION_ROUNDS = 1
MAX_SERVICES = 6
MAX_REVISION_ITEMS = 10

PAGES = ("Accueil", "À propos", "Services", "Galerie", "Contact")

PITCH = (
    "Conception de votre site offerte grâce à l'IA : vous ne payez que le nom de domaine, "
    "l'hébergement et la mise en ligne."
)
INCLUDED = (
    "Un site de 5 pages : Accueil, À propos, Services, Galerie, Contact",
    "Un modèle professionnel au choix parmi 3, et une palette de couleurs au choix parmi 4",
    "Les textes rédigés à partir de votre brief (vous les relisez)",
    "Jusqu'à 6 services présentés",
    "Bouton WhatsApp, carte Google Maps, liens vers vos réseaux sociaux, formulaire de contact",
    "Nom de domaine, hébergement et e-mail professionnel pour la première année",
    "Référencement technique de base et mesure d'audience",
    "Une série de corrections de contenu après la première version",
    f"Mise en ligne sous {DELIVERY_BUSINESS_DAYS} jours ouvrés après réception du brief complet",
)
NOT_INCLUDED = (
    "Un design sur mesure ou une maquette créée de zéro",
    "Des pages supplémentaires ou une boutique en ligne",
    "Des fonctionnalités spécifiques (réservation, espace membre, paiement en ligne)",
    "Un changement de modèle ou de couleurs après le démarrage de la production",
    "La création d'un logo, la prise de photos ou la rédaction de contenus hors du brief",
)


@dataclass(frozen=True)
class Choice:
    key: str
    name: str
    description: str


TEMPLATES = (
    Choice("CLASSIQUE", "Classique", "Sobre et rassurant : idéal pour les services et le conseil"),
    Choice("MODERNE", "Moderne", "Grandes images et sections aérées : commerces et restauration"),
    Choice("CHALEUREUX", "Chaleureux", "Couleurs douces et formes arrondies : beauté, santé, mode"),
)
PALETTES = (
    Choice("ATLANTIQUE", "Atlantique", "Bleu profond et sable"),
    Choice("SAVANE", "Savane", "Ocre, vert olive et crème"),
    Choice("ELEGANCE", "Élégance", "Noir, blanc et doré"),
    Choice("FRAICHEUR", "Fraîcheur", "Vert menthe et blanc"),
)


class RevisionCategory(StrEnum):
    """Seules corrections possibles : le contenu, jamais la forme."""

    TEXT = "TEXT"
    PHOTO = "PHOTO"
    CONTACT = "CONTACT"
    HOURS = "HOURS"


REVISION_CATEGORY_LABELS = {
    RevisionCategory.TEXT: "Corriger un texte",
    RevisionCategory.PHOTO: "Remplacer une photo",
    RevisionCategory.CONTACT: "Modifier une coordonnée",
    RevisionCategory.HOURS: "Modifier les horaires",
}

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
PhoneNumber = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{6,14}$")]
# Domaine souhaité, sans « www » ni « https » : maboutique.ci
DomainName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,40}[a-z0-9])?\.[a-z]{2,10}$",
    ),
]
TemplateKey = Annotated[
    str, StringConstraints(pattern="^(" + "|".join(t.key for t in TEMPLATES) + ")$")
]
PaletteKey = Annotated[
    str, StringConstraints(pattern="^(" + "|".join(p.key for p in PALETTES) + ")$")
]


class Service(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Short
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


class Brief(BaseModel):
    """Brouillon du brief : chaque champ est facultatif, mais toujours dans le cadre de l'offre."""

    model_config = ConfigDict(extra="forbid")

    business_name: Short | None = None
    activity: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    services: Annotated[list[Service], Field(max_length=MAX_SERVICES)] = Field(default_factory=list)
    city: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    phone: PhoneNumber | None = None
    whatsapp: PhoneNumber | None = None
    email: Annotated[str, StringConstraints(max_length=320)] | None = None
    opening_hours: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None
    ) = None
    facebook: HttpUrl | None = None
    instagram: HttpUrl | None = None
    tiktok: HttpUrl | None = None
    desired_domain: DomainName | None = None
    template: TemplateKey | None = None
    palette: PaletteKey | None = None
    has_logo: bool | None = None


def missing_fields(brief: Brief) -> list[str]:
    """Champs encore requis avant de lancer la production (vide = brief complet)."""
    missing = [
        name
        for name in ("business_name", "activity", "phone", "template", "palette", "has_logo")
        if getattr(brief, name) in (None, "")
    ]
    if not brief.services:
        missing.append("services")
    return missing


_DOMAIN_RE = re.compile(r"^(https?://)?(www\.)?")


def clean_domain(raw: str | None) -> str | None:
    """« https://www.MaBoutique.ci/ » → « maboutique.ci » (pour préremplir depuis le site)."""
    if not raw:
        return None
    return _DOMAIN_RE.sub("", raw.strip().lower()).split("/")[0] or None


def add_business_days(start: date, days: int) -> date:
    """Jours ouvrés du lundi au vendredi (jours fériés non pris en compte)."""
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current
