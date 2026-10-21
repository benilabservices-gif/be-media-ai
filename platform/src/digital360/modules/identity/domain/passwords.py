"""Politique de mot de passe (recommandations NIST SP 800-63B : longueur plutôt que complexité)."""

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

# Mots de passe très répandus, notamment en contexte francophone
_COMMON_PASSWORDS = frozenset(
    {
        "0123456789",
        "1234567890",
        "12345678910",
        "azerty1234",
        "azertyuiop",
        "motdepasse",
        "motdepasse1",
        "password12",
        "password123",
        "qwerty1234",
        "qwertyuiop",
        "soleil1234",
        "bonjour123",
    }
)


def password_problems(password: str, *, email: str) -> list[str]:
    """Codes des règles non respectées (liste vide = mot de passe accepté)."""
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append("too_short")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append("too_long")
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        problems.append("too_common")
    if lowered == email.lower() or lowered == email.lower().split("@")[0]:
        problems.append("same_as_email")
    if len(set(password)) == 1:
        problems.append("single_character")
    return problems
