from decimal import Decimal, InvalidOperation
from ipaddress import IPv6Address, ip_address
from os import environ
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


def required(name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} is required")
    return value


def csv(name: str) -> list[str]:
    return [part.strip() for part in required(name).split(",") if part.strip()]


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(environ.get(name, str(default)))
    except ValueError as error:
        raise ImproperlyConfigured(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ImproperlyConfigured(f"{name} must be between {minimum} and {maximum}")
    return value


def canonical_ip(value: str) -> str | None:
    try:
        address = ip_address(value)
    except ValueError:
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def trusted_proxies() -> frozenset[str]:
    addresses = set()
    for item in environ.get("TRUSTED_PROXY_IPS", "").split(","):
        if not item.strip():
            continue
        address = canonical_ip(item.strip())
        if address is None:
            raise ImproperlyConfigured("TRUSTED_PROXY_IPS must contain exact IP addresses")
        addresses.add(address)
    return frozenset(addresses)


def redis_url(name: str, *, production: bool) -> str:
    value = required(name)
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be a valid Redis URL") from None
    database = url.path.removeprefix("/")
    if (
        url.scheme not in {"redis", "rediss"}
        or not url.hostname
        or port == 0
        or not database.isdecimal()
        or url.query
        or url.fragment
    ):
        raise ImproperlyConfigured(f"{name} must be a Redis URL with a database number")
    if production and len(url.password or "") < 24:
        raise ImproperlyConfigured(f"{name} requires a strong Redis password in production")
    return value


def vat_rate() -> Decimal:
    try:
        value = Decimal(environ.get("VAT_RATE_PERCENT", "12.00"))
    except InvalidOperation as error:
        raise ImproperlyConfigured("VAT_RATE_PERCENT must be a decimal") from error
    if not value.is_finite() or not 0 <= value <= 100:
        raise ImproperlyConfigured("VAT_RATE_PERCENT must be between 0 and 100")
    return value
