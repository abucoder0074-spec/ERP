import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

from django.conf import settings
from django.utils.crypto import salted_hmac
from redis import Redis
from redis.exceptions import RedisError

from config.environment import canonical_ip

# Atomically check both counters and acquire both leases before checking a password.
_BEGIN = """
local retry = 0
for i = 1, 2 do
    if tonumber(redis.call('GET', KEYS[i]) or '0') >= tonumber(ARGV[1]) then
        retry = math.max(retry, redis.call('PTTL', KEYS[i]), 1)
    end
end
if retry > 0 then return retry end
if redis.call('EXISTS', KEYS[3], KEYS[4]) > 0 then return -1 end
redis.call('SET', KEYS[3], ARGV[2], 'EX', 30)
redis.call('SET', KEYS[4], ARGV[2], 'EX', 30)
return 0
"""

# A worker whose lease expired cannot mutate counters or authorize a session.
_FINISH = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] or
   redis.call('GET', KEYS[4]) ~= ARGV[1] then return 0 end
if ARGV[2] == 'success' then
    redis.call('DEL', KEYS[1], KEYS[2])
elseif ARGV[2] == 'failure' then
    for i = 1, 2 do
        if redis.call('INCR', KEYS[i]) == 1 then
            redis.call('EXPIRE', KEYS[i], ARGV[3])
        end
    end
end
redis.call('DEL', KEYS[3], KEYS[4])
return 1
"""


class LoginUnavailable(Exception):
    """A credential check cannot proceed safely."""


@dataclass
class LoginAttempt:
    retry_after: int = 0
    succeeded: bool | None = None


def login_redis() -> Redis:
    return Redis.from_url(settings.LOGIN_REDIS_URL, socket_timeout=2, socket_connect_timeout=2)


def _keys(peer: str, username: str) -> list[str]:
    peer = canonical_ip(peer) or "unknown"
    username = unicodedata.normalize("NFKC", username.strip()).casefold()
    counters = [
        f"erp:login:{kind}:{salted_hmac(kind, value, algorithm='sha256').hexdigest()}"
        for kind, value in (("peer", peer), ("username", username))
    ]
    return [*counters, *(f"{key}:lease" for key in counters)]


@contextmanager
def login_attempt(peer: str, username: str) -> Iterator[LoginAttempt]:
    """Check both limits before authentication and record the outcome atomically."""
    keys = _keys(peer, username)
    token = uuid4().hex
    try:
        with login_redis() as cache:
            result = cache.eval(_BEGIN, len(keys), *keys, settings.LOGIN_ATTEMPTS, token)
            if not isinstance(result, int) or result < 0:
                raise LoginUnavailable
            if result:
                yield LoginAttempt(retry_after=(result + 999) // 1000)
                return
            attempt = LoginAttempt()
            try:
                yield attempt
            finally:
                outcome = {True: "success", False: "failure", None: "cancel"}[attempt.succeeded]
                updated = cache.eval(
                    _FINISH, len(keys), *keys, token, outcome, settings.LOGIN_WINDOW_SECONDS
                )
                if not updated:
                    raise LoginUnavailable
    except RedisError as error:
        raise LoginUnavailable from error
