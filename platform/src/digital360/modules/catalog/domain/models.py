"""Catalogue versionné : offres, prix par devise, droits (ARCHITECTURE.md §8).

Montants en unité mineure (FCFA pour XOF/XAF, centimes pour EUR), toujours HT.
"""

from collections.abc import Iterable
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
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

# Indicatifs du continent africain (UIT, zone 2). Exclus bien qu'en +2 : 262 (La Réunion,
# Mayotte : départements français), 297 Aruba, 298 Féroé, 299 Groenland.
_AFRICAN_DIAL_PREFIXES = frozenset(
    {"20", "27", "290", "291"}
    | {str(code) for code in range(211, 259)}
    | {str(code) for code in range(260, 270) if code != 262}
)


def is_african_number(number: str) -> bool:
    """Numéro E.164 (« +2290190493132 ») d'un pays d'Afrique."""
    digits = number.removeprefix("+")
    return any(digits.startswith(prefix) for prefix in _AFRICAN_DIAL_PREFIXES)


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


class DerivedPricing(_Frozen):
    """Prix d'une devise calculé depuis une autre, pour les parités fixes (1 EUR = 655,957 XOF).

    `rate` : unités de la devise source pour UNE unité de la devise cible.
    `rounding` : CEIL_MAJOR arrondit à l'unité supérieure (138 € plutôt que 137,05 €) ;
    NEAREST_MINOR garde la valeur exacte à l'unité mineure près.
    """

    source: Currency
    rate: Annotated[Decimal, Field(gt=0)]
    rounding: Literal["CEIL_MAJOR", "NEAREST_MINOR"] = "CEIL_MAJOR"

    def convert(self, source_amount: int, target: Currency) -> int:
        source_major = Decimal(source_amount) / (10 ** CURRENCY_EXPONENT[self.source])
        target_major = source_major / self.rate
        if self.rounding == "CEIL_MAJOR":
            target_major = target_major.to_integral_value(rounding=ROUND_CEILING)
        target_minor = target_major * (10 ** CURRENCY_EXPONENT[target])
        return int(target_minor.to_integral_value(rounding=ROUND_HALF_UP))


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
    # Devises dont le prix est calculé (parité fixe) quand aucun prix explicite n'existe
    derived_pricing: dict[Currency, DerivedPricing] = Field(default_factory=dict)
    # Devise imposée dès qu'un numéro du client est hors d'Afrique (prix Europe)
    outside_africa_currency: Currency | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        codes = [product.code for product in self.products]
        if len(codes) != len(set(codes)):
            raise ValueError("codes produits en double")
        for target, rule in self.derived_pricing.items():
            if rule.source is target:
                raise ValueError(f"{target} ne peut pas être calculé depuis lui-même")
            if rule.source in self.derived_pricing:
                raise ValueError(
                    f"{target} : la devise source {rule.source} doit avoir des prix explicites"
                )
        definitions = {definition.key: definition for definition in self.entitlements}
        for product in self.products:
            for key, value in product.entitlements.items():
                definition = definitions.get(key)
                if definition is None:
                    raise ValueError(f"{product.code} : droit inconnu {key}")
                if (definition.type == "BOOLEAN") != isinstance(value, bool):
                    raise ValueError(f"{product.code} : {key} attend une valeur {definition.type}")
        return self

    def price_for(self, product: Product, currency: Currency) -> Price | None:
        """Prix explicite s'il existe, sinon prix calculé par parité fixe, sinon None."""
        explicit = product.price_in(currency)
        if explicit is not None:
            return explicit
        rule = self.derived_pricing.get(currency)
        source = product.price_in(rule.source) if rule else None
        if rule is None or source is None:
            return None
        return Price(
            currency=currency, amount=rule.convert(source.amount, currency), period=source.period
        )

    def product(self, code: str) -> Product | None:
        return next((product for product in self.products if product.code == code), None)

    def currency_for(self, country: str | None, numbers: Iterable[str | None] = ()) -> Currency:
        """Devise de facturation imposée au client, jamais choisie par lui.

        Un seul numéro (téléphone ou WhatsApp) hors d'Afrique suffit à appliquer les prix
        Europe, quel que soit le pays déclaré ; sinon, devise du pays.
        """
        if self.outside_africa_currency is not None and any(
            number and not is_african_number(number) for number in numbers
        ):
            return self.outside_africa_currency
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
