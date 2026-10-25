import logging
import os

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    settings.check()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
                log_level="warning")


if __name__ == "__main__":
    main()
