"""Prompt de conception d'un site Digital Start, rédigé à partir du brief du client.

L'équipe le reçoit en PDF quand le client envoie son brief, peut l'améliorer depuis l'admin,
puis le donne à l'outil d'IA qui génère le site. Il rappelle le cadre de l'offre pour que
l'IA ne produise rien de plus que ce que le client a payé.
"""

from dataclasses import dataclass
from datetime import date

from digital360.modules.projects.domain import offer
from digital360.modules.projects.domain.offer import Brief

# Contenu attendu sur chacune des 5 pages de l'offre
PAGE_CONTENT = {
    "Accueil": "accroche claire (ce que fait l'entreprise, pour qui, où), 3 points forts, aperçu "
    "des services, bouton d'appel à l'action vers WhatsApp ou Contact",
    "À propos": "histoire et valeurs de l'entreprise à partir de l'activité décrite, ce qui la "
    "distingue, zone servie",
    "Services": "une section par service listé ci-dessous (nom, description, bouton de contact)",
    "Galerie": "grille de photos (emplacements à remplir avec les photos du client)",
    "Contact": "coordonnées, horaires, carte Google Maps, formulaire de contact, bouton WhatsApp",
}


COUNTRY_NAMES = {
    "BJ": "Bénin", "BF": "Burkina Faso", "CI": "Côte d'Ivoire", "ML": "Mali", "NE": "Niger",
    "SN": "Sénégal", "TG": "Togo", "CM": "Cameroun", "GA": "Gabon", "CG": "Congo",
    "FR": "France",
}  # fmt: skip

# Champs obligatoires du brief, dans les mots de l'équipe
FIELD_LABELS = {
    "business_name": "nom de l'entreprise",
    "activity": "activité",
    "phone": "téléphone",
    "template": "modèle",
    "palette": "palette de couleurs",
    "has_logo": "logo fourni ou non",
    "services": "services",
}


@dataclass(frozen=True)
class PromptContext:
    """Informations hors brief : fiche entreprise et date de livraison promise."""

    sector: str | None = None
    country: str | None = None
    due_on: date | None = None


def _choice(choices: tuple[offer.Choice, ...], key: str | None) -> str:
    chosen = next((item for item in choices if item.key == key), None)
    return f"{chosen.name} ({chosen.description})" if chosen else "à définir avec le client"


def _line(label: str, value: object) -> str:
    return f"- {label} : {value if value not in (None, '') else 'non renseigné'}"


def build_design_prompt(brief: Brief, context: PromptContext) -> str:
    name = brief.business_name or "l'entreprise"
    country = COUNTRY_NAMES.get(context.country or "", context.country)
    place = ", ".join(part for part in (brief.city, country) if part)
    services = brief.services or []
    contact_lines = [
        _line("Téléphone", brief.phone),
        _line("WhatsApp", brief.whatsapp or brief.phone),
        _line("E-mail", brief.email),
        _line("Adresse", brief.address),
        _line("Ville", brief.city),
        _line("Horaires", brief.opening_hours),
    ]
    social = [
        f"- {label} : {url}"
        for label, url in (
            ("Facebook", brief.facebook),
            ("Instagram", brief.instagram),
            ("TikTok", brief.tiktok),
        )
        if url
    ]
    service_lines = [
        f"- {item.name}" + (f" : {item.description}" if item.description else "")
        for item in services
    ]
    missing = [FIELD_LABELS.get(item, item) for item in offer.missing_fields(brief)] + [
        label
        for label, value in (
            ("adresse", brief.address),
            ("horaires", brief.opening_hours),
            ("e-mail", brief.email),
            ("nom de domaine souhaité", brief.desired_domain),
        )
        if not value
    ]

    sections: list[str] = [
        f"PROMPT DE CONCEPTION : SITE DIGITAL START DE {name.upper()}",
        "RÔLE\nTu es un concepteur web senior spécialisé dans les sites vitrines de PME "
        "africaines. Tu réalises un site professionnel, rapide et pensé d'abord pour le "
        "téléphone, en français, à partir du modèle et de la palette choisis ci-dessous. "
        "Tu n'ajoutes rien qui sorte du cadre de l'offre.",
        "\n".join(
            [
                "L'ENTREPRISE",
                _line("Nom", brief.business_name),
                _line("Activité", brief.activity),
                _line("Secteur", context.sector),
                _line("Localisation", place),
            ]
        ),
        "OBJECTIF DU SITE\nDonner confiance aux visiteurs et les amener à contacter "
        f"{name} (WhatsApp, appel, formulaire). Chaque page se termine par un appel à l'action.",
        "\n".join(
            [f"PAGES (exactement {len(offer.PAGES)}, aucune autre)"]
            + [f"- {page} : {PAGE_CONTENT[page]}" for page in offer.PAGES]
        ),
        "\n".join(
            [f"SERVICES À PRÉSENTER ({len(services)}, maximum {offer.MAX_SERVICES})"]
            + (service_lines or ["- non renseigné"])
        ),
        "\n".join(
            [
                "IDENTITÉ VISUELLE",
                f"- Modèle : {_choice(offer.TEMPLATES, brief.template)}",
                f"- Palette : {_choice(offer.PALETTES, brief.palette)}",
                "- Logo : "
                + (
                    "fourni par le client (à intégrer en en-tête et pied de page)"
                    if brief.has_logo
                    else "pas de logo : composer un logotype typographique sobre avec le nom"
                ),
            ]
        ),
        "\n".join(["COORDONNÉES ET LIENS", *contact_lines, *social]),
        "FONCTIONNALITÉS INCLUSES\n- Bouton WhatsApp flottant (lien wa.me) sur toutes les "
        "pages\n- Carte Google Maps sur la page Contact\n- Formulaire de contact (nom, "
        "téléphone, message)\n- Liens vers les réseaux sociaux listés\n- Mesure d'audience",
        "RÉFÉRENCEMENT\n- Un titre et une méta-description par page, avec l'activité et la "
        "ville\n- Balisage schema.org LocalBusiness (nom, adresse, téléphone, horaires)\n"
        "- Images compressées avec un texte alternatif\n"
        f"- Nom de domaine prévu : {brief.desired_domain or 'à définir'}",
        "RÉDACTION\nTon professionnel, chaleureux et direct, phrases courtes. Rédige les "
        "textes à partir des informations ci-dessus uniquement : n'invente ni chiffres, ni "
        "avis clients, ni prix, ni références. Là où une information manque, laisse un "
        "emplacement clairement signalé [À COMPLÉTER].",
        "\n".join(
            ["CADRE DE L'OFFRE (À RESPECTER STRICTEMENT) : NE PAS PRODUIRE"]
            + [f"- {item}" for item in offer.NOT_INCLUDED]
        ),
        "LIVRABLES\n- Le site complet, responsive, prêt à être mis en ligne\n- La liste des "
        "photos à demander au client, page par page\n- La liste des emplacements "
        "[À COMPLÉTER] restants",
    ]
    if context.due_on:
        sections.append(f"DÉLAI\nLivraison promise au client le {context.due_on:%d/%m/%Y}.")
    if missing:
        sections.append(
            "À VÉRIFIER PAR L'ÉQUIPE AVANT DE LANCER\n"
            + "\n".join(f"- {item}" for item in dict.fromkeys(missing))
        )
    return "\n\n".join(sections) + "\n"
