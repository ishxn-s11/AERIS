"""Application-wide logging configuration for AERIS."""
import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once; safe to call multiple times."""
    root = logging.getLogger()
    if root.handlers:
        # Already configured (e.g., pytest imports app multiple times).
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
    # paho-mqtt logs are noisy at INFO during reconnects.
    logging.getLogger("paho.mqtt").setLevel(logging.WARNING)


logger = logging.getLogger("aeris")
