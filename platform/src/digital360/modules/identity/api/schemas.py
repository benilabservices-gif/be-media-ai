import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from digital360.modules.identity.domain.passwords import MAX_PASSWORD_LENGTH

# Numéro international E.164 : +225 07 00 00 00 00 s'écrit +2250700000000
PhoneNumber = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{6,14}$")]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class CsrfResponse(BaseModel):
    csrf_token: str


class RegisterRequest(BaseModel):
    email: EmailStr
    # La politique complète (longueur minimale, mots de passe courants) est appliquée par le
    # service, pour renvoyer toutes les raisons de refus d'un coup
    password: Annotated[str, Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)]
    full_name: FullName
    phone: PhoneNumber | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    created_at: datetime


class SessionResponse(BaseModel):
    user: UserOut
    # Nouveau jeton CSRF (rotation à la connexion), également posé en cookie
    csrf_token: str


class ProfileUpdate(BaseModel):
    full_name: FullName | None = None
    # Chaîne vide pour effacer le numéro
    phone: PhoneNumber | Annotated[str, StringConstraints(max_length=0)] | None = None
