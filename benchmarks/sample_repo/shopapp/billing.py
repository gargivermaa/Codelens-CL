"""Prices, discounts and taxes. All money values are integer cents."""
TAX_RATES = {"IN": 18, "US": 8, "DE": 19}
COUPONS = {"WELCOME10": 10, "FESTIVE25": 25}
MAX_DISCOUNT_PERCENT = 50


def calculate_discount(subtotal_cents: int, coupon_code: str | None) -> int:
    if not coupon_code:
        return 0
    percent = min(COUPONS.get(coupon_code.upper(), 0), MAX_DISCOUNT_PERCENT)
    return subtotal_cents * percent // 100


def apply_tax(amount_cents: int, country: str) -> int:
    rate = TAX_RATES.get(country)
    if rate is None:
        raise ValueError(f"unsupported country {country}")
    return amount_cents + amount_cents * rate // 100


def order_total(items: list[dict], coupon_code: str | None, country: str) -> int:
    subtotal = sum(item["price_cents"] * item["quantity"] for item in items)
    discounted = subtotal - calculate_discount(subtotal, coupon_code)
    return apply_tax(discounted, country)
