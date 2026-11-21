"""Run the local API with settings from .env / environment variables."""

from __future__ import annotations

import uvicorn

from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    bind_error = settings.api_bind_error()
    if bind_error:
        raise SystemExit(bind_error)
    uvicorn.run(
        "server.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        ssl_certfile=settings.ssl_certfile or None,
        ssl_keyfile=settings.ssl_keyfile or None,
    )


if __name__ == "__main__":
    main()
