"""Synthetic catalogue for the mock source. All brands and stores are made up."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Store:
    """A fictional store with its own price level and shipping policy."""

    code: str
    price_multiplier: float
    flat_shipping: Decimal
    free_shipping_over: Decimal | None


@dataclass(frozen=True)
class Product:
    """A fictional product. `titles` maps store code -> how that store names it."""

    sku: str
    keywords: frozenset[str]
    base_price: float
    titles: dict[str, str]


STORES: dict[str, Store] = {
    "shopmart": Store("shopmart", 1.00, Decimal("0"), None),
    "bytebazaar": Store("bytebazaar", 0.96, Decimal("6.99"), Decimal("75")),
    "dealdepot": Store("dealdepot", 1.03, Decimal("4.99"), None),
}


def _p(sku: str, keywords: str, base: float, **titles: str) -> Product:
    return Product(sku, frozenset(keywords.split()), base, titles)


CATALOG: tuple[Product, ...] = (
    _p(
        "AUR-NC700",
        "headphones noise cancelling wireless auralis",
        249.0,
        shopmart="Auralis NC700 Wireless Noise Cancelling Headphones - Black",
        bytebazaar="AURALIS NC-700 Over-Ear ANC Bluetooth Headphones (Black)",
        dealdepot="Auralis NC700 Headphones, Wireless, Noise Cancelling",
    ),
    _p(
        "AUR-BUDS2",
        "earbuds wireless auralis",
        129.0,
        shopmart="Auralis Buds 2 True Wireless Earbuds",
        bytebazaar="Auralis Buds2 TWS In-Ear Earbuds w/ Charging Case",
        dealdepot="Auralis Buds 2 Wireless Earbuds - White",
    ),
    _p(
        "KIN-K8-128",
        "phone smartphone kinetix 128gb",
        599.0,
        shopmart="Kinetix K8 Smartphone 128GB - Graphite",
        bytebazaar="Kinetix K8 5G 128 GB Unlocked Phone, Graphite",
        dealdepot="KINETIX K8 (128GB, Graphite) Unlocked",
    ),
    _p(
        "KIN-K8-256",
        "phone smartphone kinetix 256gb",
        699.0,
        shopmart="Kinetix K8 Smartphone 256GB - Graphite",
        bytebazaar="Kinetix K8 5G 256 GB Unlocked Phone, Graphite",
        dealdepot="KINETIX K8 (256GB, Graphite) Unlocked",
    ),
    _p(
        "LUM-OLED55",
        "tv television oled lumora 55",
        1299.0,
        shopmart='Lumora 55" OLED 4K Smart TV (2026)',
        bytebazaar="Lumora OLED55 55-Inch 4K UHD Smart Television",
        dealdepot="Lumora 55 inch OLED TV - 2026 Model",
    ),
    _p(
        "BRW-PRO",
        "coffee espresso machine brewix",
        449.0,
        shopmart="Brewix Pro Espresso Machine with Milk Frother",
        bytebazaar="BREWIX Pro Semi-Automatic Espresso Maker, Stainless",
        dealdepot="Brewix Pro Espresso & Cappuccino Machine",
    ),
    _p(
        "STR-RUN3",
        "shoes running stridex",
        139.0,
        shopmart="Stridex Run 3 Men's Running Shoes",
        bytebazaar="Stridex RUN3 Road Running Shoe (Men's)",
        dealdepot="Stridex Run 3 Running Sneakers - Mens",
    ),
    _p(
        "VOL-VAC9",
        "vacuum robot voltra",
        399.0,
        shopmart="Voltra V9 Robot Vacuum with Self-Empty Base",
        bytebazaar="Voltra V9 Robotic Vacuum Cleaner + Auto-Empty Dock",
        dealdepot="Voltra Robot Vacuum V9, Self Emptying",
    ),
)
