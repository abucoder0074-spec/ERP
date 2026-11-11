from os import environ
from pathlib import Path
from sys import argv
from urllib.request import Request, urlopen


def main(mode: str) -> None:
    if mode == "web":
        host = next(
            host.strip() for host in environ["DJANGO_ALLOWED_HOSTS"].split(",") if host.strip()
        )
        request = Request("http://127.0.0.1:8000/health/", headers={"Host": host})
        with urlopen(request, timeout=7) as response:
            if response.status != 200:
                raise SystemExit(1)
        return
    command = Path("/proc/1/cmdline").read_bytes().split(b"\0")
    expected = {"beat": b"beat", "bot": b"bot"}[mode]
    if expected not in command:
        raise SystemExit(1)
    if mode == "beat":
        from redis import Redis

        with Redis.from_url(environ["CELERY_BROKER_URL"], socket_timeout=2) as broker:
            if broker.ping() is not True:
                raise SystemExit(1)


if __name__ == "__main__":
    main(argv[1])
