"""Entry point for the `forno` console script. Same image, different
commands — see docs/architecture.md "Why one image with three roles".

    forno serve            # api: FastAPI + uvicorn, SSE fan-out consumer
    forno relay            # outbox-relay: poll -> publish -> mark sent
    forno project kitchen  # projector-kitchen
    forno project stats    # projector-stats
    forno migrate          # alembic upgrade head
    forno healthcheck      # used by the Dockerfile HEALTHCHECK
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import urllib.error
import urllib.request
from http import HTTPStatus
from typing import TYPE_CHECKING

from prometheus_client import start_http_server

from forno.settings import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forno")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--reload", action="store_true", help="hot-reload (dev image only)")
    subparsers.add_parser("relay")
    project = subparsers.add_parser("project")
    project.add_argument("projector", choices=["kitchen", "stats"])
    subparsers.add_parser("migrate")
    subparsers.add_parser("healthcheck")

    args = parser.parse_args(argv)

    match args.command:
        case "serve":
            return _serve(reload=args.reload)
        case "relay":
            return _relay()
        case "project":
            return _project(args.projector)
        case "migrate":
            return _migrate()
        case "healthcheck":
            return _healthcheck()
        case _:  # pragma: no cover — argparse already enforces `choices`
            raise AssertionError(args.command)


def _reload_app_factory() -> FastAPI:  # pragma: no cover - exercised only under --reload
    """Zero-arg factory uvicorn's reloader can re-import per subprocess. A
    live `FastAPI` instance can't cross that boundary; an import string can.
    """
    from forno.adapters.http.app import create_app

    return create_app(Settings())


def _serve(*, reload: bool = False) -> int:
    import uvicorn

    if reload:
        uvicorn.run(
            "forno.__main__:_reload_app_factory",
            factory=True,
            host="0.0.0.0",  # noqa: S104 - container-internal bind
            port=8000,
            reload=True,
            # Without this uvicorn watches the whole cwd (/app), including
            # .venv — thousands of files that never change. docker-compose.yml
            # only bind-mounts src/ anyway, so nothing outside it can change.
            reload_dirs=["src"],
        )
        return 0

    from forno.adapters.http.app import create_app

    settings = Settings()
    app = create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=8000)  # noqa: S104 - container-internal bind
    return 0


def _install_stop_handler(*stoppables: object) -> None:
    """SIGTERM/SIGINT -> `stop()` on each long-running loop, which then
    exits its poll loop on the next iteration."""
    loop = asyncio.get_running_loop()

    def _handle_signal() -> None:
        for stoppable in stoppables:
            stoppable.stop()  # type: ignore[attr-defined]

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)


def _relay() -> int:
    return asyncio.run(_relay_async())


async def _relay_async() -> int:
    from aiokafka import AIOKafkaProducer

    from forno.adapters.db.session import make_engine, make_session_factory
    from forno.adapters.messaging.outbox_relay import OutboxRelay
    from forno.observability.logging import configure_logging, get_logger

    settings = Settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    start_http_server(settings.metrics_port)

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap)
    await producer.start()

    relay = OutboxRelay(session_factory, producer, settings.outbox_poll_ms, settings.outbox_batch)
    _install_stop_handler(relay)

    logger.info("relay.startup")
    try:
        await relay.run_forever()
    finally:
        await producer.stop()
        await engine.dispose()
        logger.info("relay.shutdown")
    return 0


def _project(name: str) -> int:
    return asyncio.run(_project_async(name))


async def _project_async(name: str) -> int:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

    from forno.adapters.db.session import make_engine, make_session_factory
    from forno.adapters.messaging.consumers import KitchenProjector, StatsProjector
    from forno.observability.logging import configure_logging, get_logger

    settings = Settings()
    configure_logging(settings.log_level)
    logger = get_logger(__name__)
    start_http_server(settings.metrics_port)

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    projector_cls = KitchenProjector if name == "kitchen" else StatsProjector
    consumer = AIOKafkaConsumer(
        *projector_cls.topics,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id=projector_cls.group_id,
        auto_offset_reset="earliest",
        # Offsets are committed by the projector itself, after the effect is
        # durable — see consumers.Consumer.run_forever.
        enable_auto_commit=False,
    )
    dlq_producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap)
    await dlq_producer.start()

    projector = projector_cls(
        session_factory, consumer, dlq_producer, settings.consumer_max_retries
    )
    _install_stop_handler(projector)

    logger.info("projector.startup", projector=name)
    try:
        await projector.run_forever()
    finally:
        await dlq_producer.stop()
        await engine.dispose()
        logger.info("projector.shutdown", projector=name)
    return 0


def _migrate() -> int:
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    command.upgrade(config, "head")
    return 0


def _healthcheck() -> int:
    settings = Settings()
    if settings.role == "serve":
        url, timeout = "http://localhost:8000/health/live", 3
    else:
        url, timeout = f"http://localhost:{settings.metrics_port}/metrics", 3
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed localhost URL
            return 0 if response.status == HTTPStatus.OK else 1
    except (urllib.error.URLError, TimeoutError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
