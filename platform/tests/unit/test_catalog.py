from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from digital360.modules.catalog.domain.models import (
    UNLIMITED,
    Catalog,
    Currency,
    resolve_entitlements,
)
from digital360.modules.diagnostics.domain.seeds import load_catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(Path("config/seeds/catalog.v3.yaml"))


def test_should_load_catalog_with_official_xof_prices(catalog: Catalog) -> None:
    prices = {
        product.code: price.amount
        for product in catalog.products
        if (price := product.price_in(Currency.XOF))
    }

    assert prices == {
        "DIGITAL_START": 109900,
        "DIGITAL_ESSENTIAL": 45000,
        "DIGITAL_GROWTH": 75000,
        "DIGITAL_PERFORMANCE": 150000,
    }


def test_should_derive_eur_prices_from_fixed_parity_rounded_up(catalog: Catalog) -> None:
    prices = {
        product.code: price.amount
        for product in catalog.products
        if (price := catalog.price_for(product, Currency.EUR))
    }

    # Centimes, arrondis à l'euro supérieur : 109 900 / 655,957 = 167,54 € → 168 €
    assert prices == {
        "DIGITAL_START": 16800,
        "DIGITAL_ESSENTIAL": 6900,
        "DIGITAL_GROWTH": 11500,
        "DIGITAL_PERFORMANCE": 22900,
    }


def test_should_copy_xof_amounts_for_xaf_and_keep_billing_period(catalog: Catalog) -> None:
    growth = catalog.product("DIGITAL_GROWTH")
    assert growth is not None

    price = catalog.price_for(growth, Currency.XAF)

    assert price is not None
    assert (price.amount, price.period) == (75000, "MONTH")


def test_should_prefer_explicit_price_over_derived_one(catalog: Catalog) -> None:
    data = catalog.model_dump(mode="json")
    data["products"][0]["prices"].append({"currency": "EUR", "amount": 13900, "period": "NONE"})
    custom = Catalog.model_validate(data)

    price = custom.price_for(custom.products[0], Currency.EUR)

    assert price is not None and price.amount == 13900


def test_should_not_price_product_without_source_price(catalog: Catalog) -> None:
    renewal = catalog.product("ANNUAL_RENEWAL")
    assert renewal is not None

    assert catalog.price_for(renewal, Currency.EUR) is None


def test_should_reject_derivation_from_a_derived_currency(catalog: Catalog) -> None:
    data = catalog.model_dump(mode="json")
    data["derived_pricing"]["XAF"] = {
        "source": "EUR",
        "rate": "0.0015",
        "rounding": "NEAREST_MINOR",
    }

    with pytest.raises(ValidationError, match="prix explicites"):
        Catalog.model_validate(data)


def test_should_map_country_to_currency(catalog: Catalog) -> None:
    assert catalog.currency_for("CI") is Currency.XOF
    assert catalog.currency_for("CM") is Currency.XAF
    assert catalog.currency_for("FR") is Currency.EUR
    assert catalog.currency_for(None) is Currency.XOF
    assert catalog.currency_for("US") is Currency.XOF


def test_should_grant_nothing_without_sources(catalog: Catalog) -> None:
    resolved = resolve_entitlements(catalog, [])

    assert resolved["SOCIAL_MANAGEMENT"] is False
    assert resolved["CONTENT_MONTHLY_LIMIT"] == 0


def test_should_take_union_of_booleans_and_max_of_limits(catalog: Catalog) -> None:
    essential = dict(catalog.product("DIGITAL_ESSENTIAL").entitlements)  # type: ignore[union-attr]
    extra: dict[str, Any] = {"CONTENT_MONTHLY_LIMIT": 12, "EMAIL_MARKETING": True}

    resolved = resolve_entitlements(catalog, [essential, extra])

    assert resolved["CONTENT_MONTHLY_LIMIT"] == 12
    assert resolved["EMAIL_MARKETING"] is True
    assert resolved["MAINTENANCE"] is True
    assert resolved["SOCIAL_MANAGEMENT"] is False


def test_should_let_unlimited_win(catalog: Catalog) -> None:
    resolved = resolve_entitlements(
        catalog, [{"LANDING_PAGE_LIMIT": 3}, {"LANDING_PAGE_LIMIT": UNLIMITED}]
    )

    assert resolved["LANDING_PAGE_LIMIT"] == UNLIMITED


def test_should_grant_more_with_each_plan(catalog: Catalog) -> None:
    def granted(code: str) -> set[str]:
        product = catalog.product(code)
        assert product is not None
        return {key for key, value in product.entitlements.items() if value}

    assert granted("DIGITAL_ESSENTIAL") < granted("DIGITAL_GROWTH") < granted("DIGITAL_PERFORMANCE")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda d: d["products"][0]["entitlements"].update({"INCONNU": True}), "droit inconnu"),
        (lambda d: d["products"][1]["entitlements"].update({"MAINTENANCE": 3}), "BOOLEAN"),
        (
            lambda d: d["products"][0]["prices"].append(dict(d["products"][0]["prices"][0])),
            "un seul prix",
        ),
        (lambda d: d["products"][1]["prices"][0].update({"period": "NONE"}), "période"),
        (lambda d: d["products"].append(dict(d["products"][0])), "double"),
    ],
)
def test_should_reject_inconsistent_catalog(catalog: Catalog, mutation: Any, message: str) -> None:
    data = catalog.model_dump(mode="json")
    mutation(data)

    with pytest.raises(ValidationError, match=message):
        Catalog.model_validate(data)
