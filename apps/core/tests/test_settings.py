from collections.abc import Iterator
from importlib import util
from secrets import token_urlsafe

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.environment import integer, vat_rate


@pytest.fixture
def production_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    password = token_urlsafe(32)
    for name, value in {
        "APP_ENV": "production",
        "DJANGO_SECRET_KEY": token_urlsafe(48),
        "DJANGO_ALLOWED_HOSTS": "erp.example.test",
        "POSTGRES_DB": "erp",
        "POSTGRES_USER": "erp",
        "POSTGRES_PASSWORD": password,
        "POSTGRES_HOST": "db",
        "LOGIN_REDIS_URL": f"redis://:{password}@redis:6379/0",
        "CELERY_BROKER_URL": f"redis://:{password}@redis:6379/1",
    }.items():
        monkeypatch.setenv(name, value)
    yield


@pytest.mark.usefixtures("production_env")
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENV", "typo"),
        ("DJANGO_SECRET_KEY", None),
        ("DJANGO_SECRET_KEY", "weak"),
        ("DJANGO_ALLOWED_HOSTS", "*"),
        ("POSTGRES_HOST", None),
        ("POSTGRES_PASSWORD", "weak"),
        ("LOGIN_REDIS_URL", None),
        ("LOGIN_REDIS_URL", "redis://redis:6379/0"),
        ("CELERY_BROKER_URL", "https://redis:6379/1"),
        ("CELERY_BROKER_URL", "redis://redis:bad/1"),
    ],
)
def test_settings_fail_fast(monkeypatch: pytest.MonkeyPatch, name: str, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    spec = util.find_spec("config.settings")
    assert spec is not None and spec.loader is not None
    with pytest.raises(ImproperlyConfigured, match=name):
        spec.loader.exec_module(util.module_from_spec(spec))


@pytest.mark.usefixtures("production_env")
def test_production_defaults_are_secure() -> None:
    spec = util.find_spec("config.settings")
    assert spec is not None and spec.loader is not None
    production = util.module_from_spec(spec)
    spec.loader.exec_module(production)
    assert production.SECURE_SSL_REDIRECT
    assert production.SESSION_COOKIE_SECURE
    assert production.CSRF_COOKIE_SECURE
    assert production.SECURE_HSTS_SECONDS > 0


@pytest.mark.parametrize("value", ["NaN", "-1", "101", "invalid"])
def test_vat_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("VAT_RATE_PERCENT", value)
    with pytest.raises(ImproperlyConfigured, match="VAT_RATE_PERCENT"):
        vat_rate()


@pytest.mark.parametrize("value", ["0", "xyz"])
def test_integer_validation(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("WINDOW", value)
    with pytest.raises(ImproperlyConfigured, match="WINDOW"):
        integer("WINDOW", 60, 10, 900)
