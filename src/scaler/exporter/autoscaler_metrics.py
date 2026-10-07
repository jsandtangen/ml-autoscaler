from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from scaler.cost import CostModel

logger = logging.getLogger("ruby_acorn.autoscaler_metrics")


@dataclass(frozen=True)
class AutoscalerMetricsConfig:
    host: str
    port: int


@dataclass(frozen=True)
class DecisionSnapshot:
    player_count: int
    desired_instances: int
    scaling_action: int
    timestamp: float


@dataclass(frozen=True)
class GameMetrics:
    running_instances: int = 0
    strategy: str | None = None
    decision: DecisionSnapshot | None = None
    scale_up_events: int = 0
    scale_down_events: int = 0


class AutoscalerMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._games: dict[str | None, GameMetrics] = {None: GameMetrics()}
        self._cost_model = CostModel()

    def set_cost_model(self, model: CostModel) -> None:
        with self._lock:
            self._cost_model = model

    def set_running_instances(self, count: int, appid: str | None = None) -> None:
        with self._lock:
            state = self._games.get(appid, GameMetrics())
            self._games[appid] = replace(state, running_instances=count)

    def set_strategy(self, name: str, appid: str | None = None) -> None:
        with self._lock:
            state = self._games.get(appid, GameMetrics())
            self._games[appid] = replace(state, strategy=name)

    def record_decision(
        self,
        player_count: int,
        current_instances: int,
        desired_instances: int,
        running_instances: int,
        strategy: str,
        appid: str | None = None,
    ) -> None:
        action = (desired_instances > current_instances) - (
            desired_instances < current_instances
        )
        decision = DecisionSnapshot(player_count, desired_instances, action, time.time())
        with self._lock:
            previous = self._games.get(appid, GameMetrics())
            self._games[appid] = GameMetrics(
                running_instances=running_instances,
                strategy=strategy,
                decision=decision,
                scale_up_events=previous.scale_up_events + (action > 0),
                scale_down_events=previous.scale_down_events + (action < 0),
            )

    def render(self) -> bytes:
        with self._lock:
            games = dict(self._games)
            cost_model = self._cost_model
        if any(appid is not None for appid in games):
            games.pop(None, None)

        descriptions = {
            "player_count": "Player count used in the last successful decision.",
            "running_instances": "Current number of running instances reported by the controller.",
            "desired_instances": "Desired instances from the last successful decision.",
            "scaling_action": "Last successful decision: -1 down, 0 unchanged, 1 up.",
            "strategy": "Selected scaling strategy identified by the name label.",
            "last_decision_timestamp_seconds": "Unix timestamp of the last successful decision.",
            "scale_up_events_total": "Total successful scale-up operations since process start.",
            "scale_down_events_total": "Total successful scale-down operations since process start.",
            "dynamic_cost": "Estimated dynamic VM cost in EUR per hour at current allocation.",
            "fixed_baseline_cost": "Estimated fixed VM baseline cost in EUR per hour per game.",
            "savings": "Estimated savings in EUR per hour: fixed baseline minus dynamic cost.",
        }
        samples: dict[str, list[str]] = {name: [] for name in descriptions}
        for appid, state in sorted(games.items(), key=lambda item: item[0] or ""):
            labels = self._labels(appid)
            cost = cost_model.estimate_hourly(state.running_instances)
            for name, value in (
                ("dynamic_cost", cost.dynamic_cost),
                ("fixed_baseline_cost", cost.fixed_baseline_cost),
                ("savings", cost.savings),
            ):
                samples[name].append(f"{name}{labels} {value:.12g}\n")
            samples["running_instances"].append(
                f"running_instances{labels} {state.running_instances}\n"
            )
            samples["scale_up_events_total"].append(
                f"scale_up_events_total{labels} {state.scale_up_events}\n"
            )
            samples["scale_down_events_total"].append(
                f"scale_down_events_total{labels} {state.scale_down_events}\n"
            )
            if state.strategy is not None:
                strategy_labels = self._labels(appid, name=state.strategy)
                samples["strategy"].append(f"strategy{strategy_labels} 1\n")
            if state.decision is not None:
                decision = state.decision
                values = {
                    "player_count": decision.player_count,
                    "desired_instances": decision.desired_instances,
                    "scaling_action": decision.scaling_action,
                    "last_decision_timestamp_seconds": decision.timestamp,
                }
                for name, value in values.items():
                    samples[name].append(f"{name}{labels} {value}\n")

        return "".join(
            f"# HELP {name} {description}\n"
            f"# TYPE {name} {'counter' if name.endswith('_total') else 'gauge'}\n"
            + "".join(samples[name])
            for name, description in descriptions.items()
        ).encode("utf-8")

    def _labels(self, appid: str | None, **extra: str) -> str:
        labels = {"appid": appid} if appid is not None else {}
        labels.update(extra)
        if not labels:
            return ""
        return "{" + ",".join(
            f'{key}="{self._escape_label(value)}"' for key, value in labels.items()
        ) + "}"

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
