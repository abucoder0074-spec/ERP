from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.test import Client
from pytest_django.fixtures import SettingsWrapper
from redis.exceptions import ConnectionError as RedisConnectionError

from apps.core.services.login import LoginUnavailable, _keys, login_attempt, login_redis

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse

pytestmark = pytest.mark.django_db

PASSWORD = "Winter orchard 741!"
PEER = "192.0.2.10"
OTHER_PEER = "192.0.2.11"


@pytest.fixture(autouse=True)
def throttle_settings(settings: SettingsWrapper) -> Iterator[None]:
    settings.SECRET_KEY = uuid4().hex * 2
    settings.LOGIN_ATTEMPTS = 2
    settings.LOGIN_WINDOW_SECONDS = 10
    yield


@pytest.fixture
def account() -> User:
    return User.objects.create_user(username="operator", password=PASSWORD)


def sign_in(
    client: Client, name: str, password: str, peer: str = PEER
) -> _MonkeyPatchedWSGIResponse:
    return client.post("/login/", {"username": name, "password": password}, REMOTE_ADDR=peer)


def counts(peer: str, name: str) -> list[bytes | None]:
    with login_redis() as cache:
        result = cache.mget(_keys(peer, name)[:2])
    assert isinstance(result, list)
    return result


def test_only_failures_count_and_success_resets_both(client: Client, account: User) -> None:
    assert client.get("/login/").status_code == 200
    assert counts(PEER, account.username) == [None, None]
    assert sign_in(client, account.username, "wrong").status_code == 200
    assert counts(PEER, account.username) == [b"1", b"1"]
    assert sign_in(client, account.username, PASSWORD).status_code == 302
    assert counts(PEER, account.username) == [None, None]
    assert client.post("/logout/").status_code == 200
    assert sign_in(client, account.username, "wrong").status_code == 200
    assert counts(PEER, account.username) == [b"1", b"1"]


def test_next_attempt_is_rejected_before_form_or_authentication(
    client: Client, account: User
) -> None:
    for _ in range(2):
        assert sign_in(client, account.username, "wrong").status_code == 200
    with (
        patch("django.contrib.auth.forms.authenticate") as authenticate,
        patch("apps.core.views.WorkspaceLoginView.get_form") as get_form,
    ):
        response = sign_in(client, account.username, PASSWORD)
    authenticate.assert_not_called()
    get_form.assert_not_called()
    assert response.status_code == 429
    assert 1 <= int(response["Retry-After"]) <= 10
    assert response.context is not None and response.context.get("form") is None
    assert b"<form" not in response.content
    assert PASSWORD.encode() not in response.content
    assert account.username.encode() not in response.content
    assert "_auth_user_id" not in client.session
    assert counts(PEER, account.username) == [b"2", b"2"]


def test_username_limit_spans_ips_and_normalizes_case(client: Client, account: User) -> None:
    assert sign_in(client, " Operator ", "wrong", PEER).status_code == 200
    assert sign_in(client, "OPERATOR", "wrong", OTHER_PEER).status_code == 200
    assert sign_in(client, account.username, PASSWORD, "192.0.2.12").status_code == 429
    assert sign_in(client, "unrelated", "wrong", PEER).status_code == 200


def test_ip_limit_spans_usernames(client: Client, account: User) -> None:
    assert sign_in(client, "missing-1", "wrong").status_code == 200
    assert sign_in(client, "missing-2", "wrong").status_code == 200
    assert sign_in(client, account.username, PASSWORD).status_code == 429
    assert sign_in(client, account.username, PASSWORD, OTHER_PEER).status_code == 302


def test_counters_expire_without_extending_on_lockout(
    client: Client, account: User, settings: SettingsWrapper
) -> None:
    settings.LOGIN_ATTEMPTS = 1
    settings.LOGIN_WINDOW_SECONDS = 2
    assert sign_in(client, account.username, "wrong").status_code == 200
    assert sign_in(client, account.username, PASSWORD).status_code == 429
    sleep(2.05)
    assert counts(PEER, account.username) == [None, None]
    assert sign_in(client, account.username, PASSWORD).status_code == 302


def test_unknown_and_known_account_failures_share_message(client: Client, account: User) -> None:
    for name in (account.username, "missing-account"):
        response = sign_in(client, name, "wrong")
        assert response.status_code == 200
        assert response.context["form"].non_field_errors().as_data()[0].code == "invalid_login"
        assert b"Login va parolni tekshiring" in response.content


def test_redis_failure_refuses_credentials(client: Client, account: User) -> None:
    with (
        patch("apps.core.services.login.login_redis", side_effect=RedisConnectionError),
        patch("django.contrib.auth.forms.authenticate") as authenticate,
    ):
        response = sign_in(client, account.username, PASSWORD)
    assert response.status_code == 503
    authenticate.assert_not_called()


def test_overlapping_requests_cannot_check_the_same_counter(settings: SettingsWrapper) -> None:
    settings.LOGIN_ATTEMPTS = 1

    def overlapping_attempt() -> None:
        with (
            pytest.raises(LoginUnavailable),
            login_attempt(OTHER_PEER, " OPERATOR "),
        ):
            pytest.fail("A concurrent request acquired a lease for the same account")

    with ThreadPoolExecutor(max_workers=1) as executor, login_attempt(PEER, "operator") as first:
        assert first.retry_after == 0
        executor.submit(overlapping_attempt).result(timeout=5)
        first.succeeded = False
    with login_attempt(OTHER_PEER, "operator") as blocked:
        assert blocked.retry_after > 0


def test_cancelled_attempt_releases_both_leases() -> None:
    with pytest.raises(RuntimeError, match="abort"), login_attempt(PEER, "operator"):
        raise RuntimeError("abort")
    assert counts(PEER, "operator") == [None, None]
    with login_attempt(PEER, "operator") as retry:
        assert retry.retry_after == 0


def test_expired_lease_cannot_commit_success() -> None:
    with pytest.raises(LoginUnavailable), login_attempt(PEER, "operator") as attempt:
        with login_redis() as cache:
            cache.delete(*_keys(PEER, "operator")[2:])
        attempt.succeeded = True


def test_only_trusted_proxy_can_supply_client_ip(client: Client, settings: SettingsWrapper) -> None:
    settings.LOGIN_ATTEMPTS = 1
    settings.TRUSTED_PROXY_IPS = frozenset({PEER})
    data = {"username": "unknown", "password": "invalid"}
    assert (
        client.post(
            "/login/", data, REMOTE_ADDR=PEER, HTTP_X_REAL_IP="::ffff:192.0.2.30"
        ).status_code
        == 200
    )
    assert counts("192.0.2.30", "unknown") == [b"1", b"1"]
    assert counts(PEER, "unknown") == [None, b"1"]

    assert (
        client.post(
            "/login/",
            {"username": "untrusted", "password": "invalid"},
            REMOTE_ADDR=OTHER_PEER,
            HTTP_X_REAL_IP="192.0.2.31",
        ).status_code
        == 200
    )
    assert counts(OTHER_PEER, "untrusted") == [b"1", b"1"]
    assert counts("192.0.2.31", "untrusted") == [None, b"1"]
