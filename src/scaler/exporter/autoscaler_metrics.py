from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

logger = logging.getLogger("ruby_acorn.autoscaler_metrics")


@dataclass(frozen=True)
class AutoscalerMetricsConfig:
    host: str
    port: int


class AutoscalerMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running_instances = 0
        self._game_instances: dict[str, int] = {}

    def set_running_instances(self, count: int, appid: str | None = None) -> None:
        with self._lock:
            if appid is None:
                self._running_instances = count
            else:
                self._game_instances[appid] = count

    def render(self) -> bytes:
        with self._lock:
            running_instances = self._running_instances
            game_instances = dict(self._game_instances)

        if game_instances:
            samples = "".join(
                f'running_instances{{appid="{self._escape_label(appid)}"}} {count}\n'
                for appid, count in sorted(game_instances.items())
            )
        else:
            samples = f"running_instances {running_instances}\n"

        body = (
            "# HELP running_instances Current number of fake VM instances.\n"
            "# TYPE running_instances gauge\n"
            f"{samples}"
        )
        return body.encode("utf-8")

    @staticmethod
    def _escape_label(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def build_handler(metrics: AutoscalerMetrics) -> type[BaseHTTPRequestHandler]:
    class AutoscalerMetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path

            if path == "/health":
                self._write_response(HTTPStatus.OK, b"ok\n", "text/plain")
                return

            if path != "/metrics":
                self._write_response(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
                return

            self._write_response(
                HTTPStatus.OK,
                metrics.render(),
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

    return AutoscalerMetricsHandler


def start_metrics_server(
    config: AutoscalerMetricsConfig,
    metrics: AutoscalerMetrics,
    server_class: type[ThreadingHTTPServer] = ThreadingHTTPServer,
) -> ThreadingHTTPServer:
    handler = build_handler(metrics)
    server = server_class((config.host, config.port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    logger.info(
        "Started autoscaler metrics exporter host=%s port=%s",
        config.host,
        config.port,
    )
    return server
