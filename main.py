"""DesktopWaifu entry point."""
import logging
import sys

from waifu.config import APP_DIR


def main() -> int:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(APP_DIR / "desktopwaifu.log", encoding="utf-8")]
    if sys.stdout is not None:  # pythonw has no stdout
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )

    from waifu.app import run
    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
