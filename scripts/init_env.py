import os
from pathlib import Path
from secrets import token_urlsafe


def private_file(path: str, flags: int) -> int:
    return os.open(path, flags, 0o600)


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    target = root / ".env"
    content = (root / ".env.example").read_text(encoding="utf-8")
    for placeholder in (
        "GENERATE_DJANGO_SECRET",
        "GENERATE_DB_PASSWORD",
        "GENERATE_REDIS_PASSWORD",
    ):
        content = content.replace(placeholder, token_urlsafe(48))
    try:
        with open(target, "x", encoding="utf-8", opener=private_file) as output:
            output.write(content)
    except FileExistsError:
        raise SystemExit(".env already exists") from None


if __name__ == "__main__":
    main()
