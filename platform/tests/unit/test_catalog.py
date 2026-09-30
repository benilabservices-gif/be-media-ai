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
    return load_catalog(Path("config/seeds/catalog.v1.yaml"))


def test_should_load_v1_catalog_with_official_xof_prices(catalog: Catalog) -> None:
    prices = {
        product.code: price.amount
        for product in catalog.products
        if (price := product.price_in(Currency.XOF))
    }

    assert prices == {
        "DIGITAL_START": 89900,
        "DIGITAL_ESSENTIAL": 25000,
        "DIGITAL_GROWTH": 50000,
        "DIGITAL_PERFORMANCE": 100000,
    }


def test_should_not_sell_anything_in_currency_without_prices(catalog: Catalog) -> None:
    assert all(product.price_in(Currency.EUR) is None for product in catalog.products)


def test_should_map_country_to_currency(catalog: Catalog) -> None:
    assert catalog.currency_for_country("CI") is Currency.XOF
    assert catalog.currency_for_country("CM") is Currency.XAF
    assert catalog.currency_for_country("FR") is Currency.EUR
    assert catalog.currency_for_country(None) is Currency.XOF
    assert catalog.currency_for_country("US") is Currency.XOF


def test_should_grant_nothing_without_sources(catalog: Catalog) -> None:
    resolved = resolve_entitlements(catalog, [])

    assert resolved["SOCIAL_MANAGEMENT"] is False
    assert resolved["CONTENT_MONTHLY_LIMIT"] == 0


def test_should_take_union_of_booleans_and_max_of_limits(catalog: Catalog) -> None:
    essential = dict(catalog.product("DIGITAL_ESSENTIAL").entitlements)  # type: ignore[union-attr]
    extra: dict[str, Any] = {"CONTENT_MONTHLY_LIMIT": 6, "EMAIL_MARKETING": True}

    resolved = resolve_entitlements(catalog, [essential, extra])

    assert resolved["CONTENT_MONTHLY_LIMIT"] == 6
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
