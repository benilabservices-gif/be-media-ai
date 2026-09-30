"""Catalogue versionné : offres, prix par devise, droits (ARCHITECTURE.md §8).

Montants en unité mineure (FCFA pour XOF/XAF, centimes pour EUR), toujours HT.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Currency(StrEnum):
    XOF = "XOF"
    XAF = "XAF"
    EUR = "EUR"


CURRENCY_EXPONENT = {Currency.XOF: 0, Currency.XAF: 0, Currency.EUR: 2}


class BillingPeriod(StrEnum):
    NONE = "NONE"
    MONTH = "MONTH"
    YEAR = "YEAR"


class ProductKind(StrEnum):
    ONE_TIME = "ONE_TIME"
    SUBSCRIPTION = "SUBSCRIPTION"
    ADDON = "ADDON"


# Un droit est soit un interrupteur (booléen), soit une limite (entier ou illimité)
UNLIMITED = "UNLIMITED"
EntitlementValue = bool | Annotated[int, Field(ge=0)] | Literal["UNLIMITED"]


class Price(_Frozen):
    currency: Currency
    amount: Annotated[int, Field(ge=0)]
    period: BillingPeriod = BillingPeriod.NONE


class EntitlementDefinition(_Frozen):
    key: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
    type: Literal["BOOLEAN", "LIMIT"]
    description: str


class Product(_Frozen):
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
    name: str
    kind: ProductKind
    module: Literal["PRESENCE", "VISIBILITY", "ACQUISITION", "CONVERSION", "RETENTION", "ANALYTICS"]
    # Offres d'une même famille mutuellement exclusives (un seul accompagnement à la fois)
    family: str | None = None
    description: str
    includes: tuple[str, ...] = ()
    public: bool = True
    prices: tuple[Price, ...] = ()
    entitlements: dict[str, EntitlementValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_prices(self) -> Self:
        currencies = [price.currency for price in self.prices]
        if len(currencies) != len(set(currencies)):
            raise ValueError(f"{self.code} : un seul prix par devise")
        expected = BillingPeriod.NONE if self.kind is not ProductKind.SUBSCRIPTION else None
        for price in self.prices:
            if expected is not None and price.period is not expected:
                raise ValueError(f"{self.code} : un produit {self.kind} n'a pas de période")
            if expected is None and price.period is BillingPeriod.NONE:
                raise ValueError(f"{self.code} : un abonnement a une période (MONTH ou YEAR)")
        return self

    def price_in(self, currency: Currency) -> Price | None:
        return next((price for price in self.prices if price.currency is currency), None)


class Catalog(_Frozen):
    key: str
    version: int
    default_currency: Currency
    currency_by_country: dict[Annotated[str, Field(pattern=r"^[A-Z]{2}$")], Currency]
    entitlements: tuple[EntitlementDefinition, ...]
    products: tuple[Product, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        codes = [product.code for product in self.products]
        if len(codes) != len(set(codes)):
            raise ValueError("codes produits en double")
        definitions = {definition.key: definition for definition in self.entitlements}
        for product in self.products:
            for key, value in product.entitlements.items():
                definition = definitions.get(key)
                if definition is None:
                    raise ValueError(f"{product.code} : droit inconnu {key}")
                if (definition.type == "BOOLEAN") != isinstance(value, bool):
                    raise ValueError(f"{product.code} : {key} attend une valeur {definition.type}")
        return self

    def product(self, code: str) -> Product | None:
        return next((product for product in self.products if product.code == code), None)

    def currency_for_country(self, country: str | None) -> Currency:
        return self.currency_by_country.get(country or "", self.default_currency)


def resolve_entitlements(
    catalog: Catalog, grants: list[dict[str, EntitlementValue]]
) -> dict[str, EntitlementValue]:
    """Combine les droits accordés par plusieurs sources : OU pour les booléens, maximum pour
    les limites (UNLIMITED l'emporte). Un droit jamais accordé vaut False ou 0."""
    resolved: dict[str, EntitlementValue] = {}
    for definition in catalog.entitlements:
        values = [grant[definition.key] for grant in grants if definition.key in grant]
        if definition.type == "BOOLEAN":
            resolved[definition.key] = any(value is True for value in values)
        elif UNLIMITED in values:
            resolved[definition.key] = UNLIMITED
        else:
            resolved[definition.key] = max(
                (
                    value
                    for value in values
                    if isinstance(value, int) and not isinstance(value, bool)
                ),
                default=0,
            )
    return resolved
