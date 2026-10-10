"""Devise imposée par la région : un client hors d'Afrique paie toujours les prix Europe."""

from pathlib import Path

import pytest

from digital360.modules.catalog.domain.models import Catalog, Currency, is_african_number
from digital360.modules.diagnostics.domain.seeds import load_catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(Path("config/seeds/catalog.v4.yaml"))


@pytest.mark.parametrize(
    "number",
    [
        "+2290190493132",  # Bénin
        "+2250700000000",  # Côte d'Ivoire
        "+241062758438",  # Gabon
        "+212600000000",  # Maroc
        "+201000000000",  # Égypte
        "+27820000000",  # Afrique du Sud
        "+2348000000000",  # Nigeria
    ],
)
def test_should_recognize_african_numbers(number: str) -> None:
    assert is_african_number(number)


@pytest.mark.parametrize(
    "number",
    [
        "+33612345678",  # France
        "+32470000000",  # Belgique
        "+41790000000",  # Suisse
        "+15145550000",  # Canada
        "+262692000000",  # La Réunion : département français, prix Europe
        "+2975600000",  # Aruba : indicatif en +2 mais hors d'Afrique
        "+299320000",  # Groenland
    ],
)
def test_should_not_count_other_numbers_as_african(number: str) -> None:
    assert not is_african_number(number)


def test_should_impose_europe_prices_when_a_number_is_outside_africa(catalog: Catalog) -> None:
    # Pays déclaré africain, mais WhatsApp français : prix Europe, sans échappatoire
    assert catalog.currency_for("CI", ("+2250700000000", "+33612345678")) is Currency.EUR


def test_should_keep_country_currency_for_african_numbers(catalog: Catalog) -> None:
    assert catalog.currency_for("CI", ("+2250700000000", None)) is Currency.XOF
    assert catalog.currency_for("GA", ("+241062758438",)) is Currency.XAF


def test_should_fall_back_to_country_without_number(catalog: Catalog) -> None:
    assert catalog.currency_for("FR", (None, None)) is Currency.EUR
    assert catalog.currency_for("BJ", ()) is Currency.XOF


def test_should_price_europe_offers_with_market_prices(catalog: Catalog) -> None:
    prices = {
        product.code: (price.amount, price.period)
        for product in catalog.products
        if product.public and (price := catalog.price_for(product, Currency.EUR))
    }

    # Centimes HT, prix fixés d'après l'étude de marché du 2026-10-10
    assert prices == {
        "DIGITAL_START": (69000, "NONE"),
        "DIGITAL_ESSENTIAL": (29000, "MONTH"),
        "DIGITAL_GROWTH": (49000, "MONTH"),
        "DIGITAL_PERFORMANCE": (99000, "MONTH"),
    }


def test_should_keep_african_prices_unchanged(catalog: Catalog) -> None:
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
