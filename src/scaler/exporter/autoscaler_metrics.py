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

    def set_running_instances(self, count: int) -> None:
        with self._lock:
            self._running_instances = count

    def render(self) -> bytes:
        with self._lock:
            running_instances = self._running_instances

        body = (
            "# HELP running_instances Current number of fake VM instances.\n"
            "# TYPE running_instances gauge\n"
            f"running_instances {running_instances}\n"
        )
        return body.encode("utf-8")


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
