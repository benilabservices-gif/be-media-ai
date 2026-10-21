from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


class Argon2PasswordHasher:
    """Argon2id avec les paramètres par défaut d'argon2-cffi (profil RFC 9106 bas-mémoire)."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher()
        # Sert à comparer un mot de passe même quand l'email est inconnu : le temps de
        # réponse ne révèle alors pas si un compte existe
        self.dummy_hash = self._hasher.hash("digital360-dummy-password")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)
