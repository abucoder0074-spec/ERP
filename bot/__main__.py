import logging
from signal import SIGINT, SIGTERM, signal
from threading import Event
from types import FrameType

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the Phase 0 bot service without connecting to Telegram."""
    shutdown = Event()

    def stop(signum: int, frame: FrameType | None) -> None:
        shutdown.set()

    signal(SIGINT, stop)
    signal(SIGTERM, stop)
    logging.basicConfig(level=logging.INFO)
    logger.info("Telegram integration is scheduled for Phase 5")
    shutdown.wait()


if __name__ == "__main__":
    main()
