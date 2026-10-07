from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_STEAM_API_URL = (
    "https://api.steampowered.com/ISteamUserStats/"
    "GetNumberOfCurrentPlayers/v1/"
)
DEFAULT_APPID = "730"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_METRIC_NAME = "steam_player_count"
DEFAULT_TIMEOUT_SECONDS = 5.0

logger = logging.getLogger("ruby_acorn.steam_player_exporter")


class SteamPlayerExporterError(Exception):
    """Base error raised by the Steam player exporter."""


class SteamPlayerFetchError(SteamPlayerExporterError):
    """Raised when player counts cannot be fetched from Steam."""


@dataclass(frozen=True)
class SteamPlayerExporterConfig:
    appid: str = DEFAULT_APPID
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    metric_name: str = DEFAULT_METRIC_NAME
    steam_api_url: str = DEFAULT_STEAM_API_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


class SteamPlayerClient:
    def __init__(
        self,
        api_url: str = DEFAULT_STEAM_API_URL,
        session: requests.Session | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.api_url = api_url
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def get_current_players(self, appid: str) -> int:
        try:
            response = self.session.get(
                self.api_url,
                params={"appid": appid},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as error:
            raise SteamPlayerFetchError(f"Steam request failed: {error}") from error
        except json.JSONDecodeError as error:
            raise SteamPlayerFetchError("Steam returned invalid JSON") from error

        return parse_current_players(payload)


def parse_current_players(payload: Any) -> int:
    if not isinstance(payload, dict):
        raise SteamPlayerFetchError("Steam response must be a JSON object")

    response = payload.get("response")
    if not isinstance(response, dict):
        raise SteamPlayerFetchError("Steam response is missing response object")

    result = response.get("result")
    if result not in (1, "1", None):
        raise SteamPlayerFetchError(f"Steam response reported result={result}")

    player_count = response.get("player_count")
    if player_count is None:
        raise SteamPlayerFetchError("Steam response is missing player_count")

    try:
        parsed = int(player_count)
    except (TypeError, ValueError) as error:
        raise SteamPlayerFetchError(
            f"Steam player_count is not an integer: {player_count!r}"
        ) from error

    if parsed < 0:
        raise SteamPlayerFetchError("Steam player_count cannot be negative")

    return parsed


def prometheus_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_metrics(metric_name: str, appid: str, player_count: int) -> bytes:
    escaped_appid = prometheus_label_value(appid)
    body = (
        f"# HELP {metric_name} Current Steam player count.\n"
        f"# TYPE {metric_name} gauge\n"
        f'{metric_name}{{appid="{escaped_appid}"}} {player_count}\n'
    )
    return body.encode("utf-8")


def build_handler(
    client: SteamPlayerClient,
    appid: str,
    metric_name: str,
) -> type[BaseHTTPRequestHandler]:
    class SteamPlayerMetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path

            if path == "/health":
                self._write_response(HTTPStatus.OK, b"ok\n", "text/plain")
                return

            if path != "/metrics":
                self._write_response(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
                return

            try:
                player_count = client.get_current_players(appid)
                body = render_metrics(metric_name, appid, player_count)
            except SteamPlayerExporterError as error:
                logger.warning("Unable to export Steam player count: %s", error)
                self._write_response(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    f"{error}\n".encode("utf-8"),
                    "text/plain",
                )
                return

            self._write_response(
                HTTPStatus.OK,
                body,
                "text/plain; version=0.0.4; charset=utf-8",
            )

        def log_message(self, format: str, *args: object) -> None:
            logger.info("%s - %s", self.address_string(), format % args)

        def _write_response(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SteamPlayerMetricsHandler


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Expected a number, got {value}") from error

    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be greater than zero")

    return parsed


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Expected an integer, got {value}") from error

    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be greater than zero")

    return parsed


def load_config(argv: Sequence[str] | None = None) -> SteamPlayerExporterConfig:
    parser = argparse.ArgumentParser(
        description="Expose Steam current player counts in Prometheus format."
    )
    parser.add_argument(
        "--appid",
        default=os.getenv("STEAM_APPID", DEFAULT_APPID),
        help="Steam app id to query. Can also be set with STEAM_APPID.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("STEAM_EXPORTER_HOST", DEFAULT_HOST),
        help="Exporter bind host. Can also be set with STEAM_EXPORTER_HOST.",
    )
    parser.add_argument(
        "--port",
        type=positive_int,
        default=positive_int(os.getenv("STEAM_EXPORTER_PORT", str(DEFAULT_PORT))),
        help="Exporter port. Can also be set with STEAM_EXPORTER_PORT.",
    )
    parser.add_argument(
        "--metric-name",
        default=os.getenv("STEAM_EXPORTER_METRIC_NAME", DEFAULT_METRIC_NAME),
        help="Prometheus metric name. Can also be set with STEAM_EXPORTER_METRIC_NAME.",
    )
    parser.add_argument(
        "--steam-api-url",
        default=os.getenv("STEAM_API_URL", DEFAULT_STEAM_API_URL),
        help="Steam player-count API URL. Can also be set with STEAM_API_URL.",
    )
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=positive_float(
            os.getenv("STEAM_REQUEST_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
        ),
        help=(
            "Seconds to wait for Steam API responses. "
            "Can also be set with STEAM_REQUEST_TIMEOUT_SECONDS."
        ),
    )

    args = parser.parse_args(argv)
    return SteamPlayerExporterConfig(
        appid=args.appid,
        host=args.host,
        port=args.port,
        metric_name=args.metric_name,
        steam_api_url=args.steam_api_url,
        timeout_seconds=args.timeout,
    )


def run_exporter(
    config: SteamPlayerExporterConfig,
    server_class: type[ThreadingHTTPServer] = ThreadingHTTPServer,
) -> None:
    client = SteamPlayerClient(
        config.steam_api_url,
        timeout_seconds=config.timeout_seconds,
    )
    handler = build_handler(client, config.appid, config.metric_name)
    server = server_class((config.host, config.port), handler)

    logger.info(
        "Starting Steam player exporter host=%s port=%s appid=%s metric_name=%s",
        config.host,
        config.port,
        config.appid,
        config.metric_name,
    )
    server.serve_forever()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    config = load_config(argv)

    try:
        run_exporter(config)
    except KeyboardInterrupt:
        logger.info("Steam player exporter stopped")

    return 0
