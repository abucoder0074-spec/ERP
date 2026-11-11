from pathlib import Path
from sys import argv
from urllib.request import Request, urlopen

import django
from django.conf import settings
from redis import Redis


def main(mode: str) -> None:
    if mode == "web":
        request = Request(
            "http://127.0.0.1:8000/health/",
            headers={"Host": settings.ALLOWED_HOSTS[0]},
        )
        with urlopen(request, timeout=4) as response:
            if response.status != 200:
                raise SystemExit(1)
        return
    command = Path("/proc/1/cmdline").read_bytes().split(b"\0")
    expected = {"beat": b"beat", "bot": b"bot"}[mode]
    if expected not in command:
        raise SystemExit(1)
    if mode == "beat":
        with Redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=2) as broker:
            if broker.ping() is not True:
                raise SystemExit(1)


if __name__ == "__main__":
    django.setup()
    main(argv[1])
