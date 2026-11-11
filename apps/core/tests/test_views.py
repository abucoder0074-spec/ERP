from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, User
from django.db import DatabaseError, connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils.formats import date_format, number_format
from django.utils.translation import override
from pytest_django.fixtures import SettingsWrapper
from redis.exceptions import ConnectionError as RedisConnectionError

pytestmark = pytest.mark.django_db

ROLES = [
    "Admin",
    "Procurement Manager",
    "Procurement Specialist",
    "Warehouse Keeper",
    "Accountant",
    "Production Planner",
    "Viewer",
]


@pytest.mark.parametrize("role", ROLES)
def test_every_role_can_use_phase_zero_views(client: Client, role: str) -> None:
    user = User.objects.create_user(username="operator")
    user.groups.add(Group.objects.get(name=role))
    password = "Winter orchard 741!"
    user.set_password(password)
    user.save(update_fields=["password"])
    assert (
        client.post("/login/", {"username": user.username, "password": password}).status_code == 302
    )
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert b'href="/" hx-boost="true"' in dashboard.content
    assert "no-store" in dashboard["Cache-Control"]
    assert client.get("/login/").status_code == 200
    assert client.get("/health/").json() == {"status": "ok"}
    assert client.get("/manifest.webmanifest").status_code == 200
    worker = client.get("/service-worker.js")
    assert worker.status_code == 200
    assert b"erp-public-" in worker.content
    assert client.get("/offline/").status_code == 200
    language = client.post("/language/", {"language": "en", "next": "/"})
    assert language.status_code == 302
    assert language.cookies["django_language"].value == "en"
    assert client.get("/logout/").status_code == 405
    logout = client.post("/logout/")
    assert logout.status_code == 200
    assert b"Signed out" in logout.content
    assert client.get("/")["Location"] == "/login/?next=/"


def test_anonymous_and_roleless_permissions(client: Client) -> None:
    assert client.get("/")["Location"] == "/login/?next=/"
    assert client.get("/login/").status_code == 200
    assert client.get("/health/").status_code == 200
    assert client.get("/manifest.webmanifest").status_code == 200
    worker = client.get("/service-worker.js")
    assert worker.status_code == 200
    assert b"erp-public-" in worker.content
    assert client.get("/offline/").status_code == 200
    assert client.post("/language/", {"language": "uz", "next": "/login/"}).status_code == 302
    client.force_login(User.objects.create_user(username="roleless"))
    denied = client.get("/")
    assert denied.status_code == 403
    assert b'href="/" hx-boost="true"' not in denied.content
    assert b'action="/logout/"' in denied.content
    assert client.post("/logout/").status_code == 200


def test_dashboard_permission_query_count(client: Client) -> None:
    user = User.objects.create_user(username="viewer")
    user.groups.add(Group.objects.get(name="Viewer"))
    client.force_login(user)
    with CaptureQueriesContext(connection) as queries:
        assert client.get("/").status_code == 200
    assert len(queries) == 5


@pytest.mark.parametrize("path", ["/login/", "/logout/", "/language/"])
def test_form_posts_require_csrf(path: str) -> None:
    client = Client(enforce_csrf_checks=True)
    assert client.post(path).status_code == 403


@pytest.mark.parametrize(
    ("target", "error"),
    [
        ("apps.core.views.connection.cursor", DatabaseError("private-db-name")),
        ("apps.core.views.login_redis", RedisConnectionError("private-redis-name")),
    ],
)
def test_readiness_failure_is_generic(client: Client, target: str, error: Exception) -> None:
    with patch(target, side_effect=error):
        response = client.get("/health/")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "no-store" in response["Cache-Control"]


def test_unexpected_redis_reply_fails_readiness(client: Client) -> None:
    with patch("apps.core.views.login_redis") as connect:
        connect.return_value.__enter__.return_value.ping.return_value = False
        assert client.get("/health/").status_code == 503


def test_readiness_public_and_read_only(client: Client) -> None:
    response = client.get("/health/")
    assert response.json() == {"status": "ok"}
    assert response["X-Request-ID"]
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]
    assert client.head("/health/").status_code == 200
    assert client.post("/health/").status_code == 405


@pytest.fixture
def production_errors(settings: SettingsWrapper) -> Iterator[None]:
    settings.ROOT_URLCONF = "apps.core.tests.error_urls"
    settings.DEBUG = False
    yield


@pytest.mark.usefixtures("production_errors")
def test_error_pages_use_uzbek(client: Client) -> None:
    client.raise_request_exception = False
    for path, status, heading in (
        ("/missing/", 404, "Sahifa topilmadi"),
        ("/test-error/", 500, "Xatolik yuz berdi"),
    ):
        response = client.get(path)
        assert response.status_code == status
        assert heading.encode() in response.content


def test_default_locale_and_formats(client: Client) -> None:
    response = client.get("/login/", HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
    assert b'<html lang="uz">' in response.content
    assert b">Parol</label>" in response.content
    assert client.post("/language/", {"language": "en", "next": "/login/"}).status_code == 302
    assert b'<html lang="en">' in client.get("/login/").content
    assert b'<html lang="uz">' in client.get("/offline/").content
    with override("uz"):
        assert date_format(date(2026, 11, 11)) == "11.11.2026"
        assert number_format(Decimal("1234567.00")) == "1 234 567,00"
