import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from digital360.core.pagination import PageInfo
from digital360.core.permissions import ClientRole
from digital360.modules.identity.api.schemas import PhoneNumber
from digital360.modules.organizations.infrastructure.models import OrganizationStatus

Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]  # ISO 3166-1 alpha-2
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]
Url = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=300)]

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HOURS = r"^(closed|([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d(,([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d)?)$"


def _check_business_hours(value: dict[str, str] | None) -> dict[str, str] | None:
    """Format : {"mon": "08:00-12:00,14:00-18:00", "sun": "closed"}."""
    if value is None:
        return None
    for day, hours in value.items():
        if day not in DAYS:
            raise ValueError(f"jour inconnu : {day} (attendu : {', '.join(DAYS)})")
        if not re.match(_HOURS, hours):
            raise ValueError(f"{day} : format attendu « 08:00-18:00 » ou « closed »")
    return value


class _OrganizationFields(BaseModel):
    legal_name: Text200 | None = None
    sector: Annotated[str, StringConstraints(max_length=50)] | None = None
    sub_sector: Annotated[str, StringConstraints(max_length=100)] | None = None
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    city: Annotated[str, StringConstraints(max_length=100)] | None = None
    address: Annotated[str, StringConstraints(max_length=300)] | None = None
    phone: PhoneNumber | None = None
    whatsapp: PhoneNumber | None = None
    email: EmailStr | None = None
    website: Url | None = None
    primary_color: HexColor | None = None
    secondary_color: HexColor | None = None
    business_hours: dict[str, str] | None = None

    _hours = field_validator("business_hours")(_check_business_hours)


class OrganizationCreate(_OrganizationFields):
    """Sans diagnostic : `commercial_name` et `country` obligatoires. Avec `diagnostic_id`
    (et l'en-tête X-Diagnostic-Token), les champs absents sont repris des réponses."""

    commercial_name: Text200 | None = None
    country: CountryCode | None = None
    diagnostic_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _required_without_diagnostic(self) -> Self:
        if self.diagnostic_id is None and (self.commercial_name is None or self.country is None):
            raise ValueError("commercial_name et country sont obligatoires sans diagnostic_id")
        return self


class OrganizationUpdate(_OrganizationFields):
    """PATCH : seuls les champs envoyés sont modifiés ; `null` efface une valeur facultative."""

    commercial_name: Text200 | None = None
    country: CountryCode | None = None

    @field_validator("commercial_name", "country")
    @classmethod
    def _required_fields_not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("ce champ ne peut pas être effacé")
        return value


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    commercial_name: str
    legal_name: str | None
    sector: str | None
    sub_sector: str | None
    description: str | None
    country: str
    city: str | None
    address: str | None
    phone: str | None
    whatsapp: str | None
    email: str | None
    website: str | None
    primary_color: str | None
    secondary_color: str | None
    business_hours: dict[str, str] | None
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime


class MemberOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: str
    role: ClientRole
    joined_at: datetime


class MemberList(BaseModel):
    data: list[MemberOut]


class OrganizationPage(BaseModel):
    data: list[OrganizationOut]
    page: PageInfo


class AdminOrganizationFilters(BaseModel):
    status: OrganizationStatus | None = None
    q: Annotated[str, Field(max_length=100)] | None = None
