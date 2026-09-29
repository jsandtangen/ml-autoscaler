from dataclasses import dataclass

import requests


class GameDataClientError(Exception):
    """Base exception for game data client errors."""


class PrometheusQueryError(GameDataClientError):
    """Raised when the Prometheus HTTP request or query fails."""


class PrometheusResponseError(GameDataClientError):
    """Raised when Prometheus returns an unexpected response shape."""


class PlayerCountNotFoundError(GameDataClientError):
    """Raised when no player count series match the query."""


@dataclass(frozen=True)
class PlayerCountSample:
    metric: dict[str, str]
    player_count: int


class GameDataClient:
    def __init__(
        self,
        prometheus_url: str,
        timeout: int | float = 5,
        session=None,
        metric_name: str = "steam_player_count",
    ):
        self.prometheus_url = prometheus_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.metric_name = metric_name

    def get_player_counts(
        self,
        label_filters: dict[str, str] | None = None,
    ) -> list[PlayerCountSample]:
        query = self._build_query(label_filters)
        payload = self._query_prometheus(query)
        results = self._extract_results(payload)

        if not results:
            raise PlayerCountNotFoundError(f"No player data found for query: {query}")

        return [self._to_player_count_sample(result) for result in results]

    def get_player_count(self, label_filters: dict[str, str] | None = None) -> int:
        samples = self.get_player_counts(label_filters)

        if len(samples) != 1:
            raise PrometheusResponseError(
                f"Expected exactly one player count series, got {len(samples)}"
            )

        return samples[0].player_count

    def _build_query(self, label_filters: dict[str, str] | None) -> str:
        if not label_filters:
            return self.metric_name

        labels = ",".join(
            f'{key}="{self._escape_label_value(value)}"'
            for key, value in sorted(label_filters.items())
        )
        return f"{self.metric_name}{{{labels}}}"

    def _query_prometheus(self, query: str) -> dict:
        url = f"{self.prometheus_url}/api/v1/query"

        try:
            response = self.session.get(
                url,
                params={"query": query},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as error:
            raise PrometheusQueryError(
                f"Failed to query Prometheus at {url}: {error}"
            ) from error
        except ValueError as error:
            raise PrometheusResponseError(
                "Prometheus returned a response that was not valid JSON"
            ) from error

        if payload.get("status") != "success":
            error_type = payload.get("errorType", "unknown")
            error_message = payload.get("error", "no error message")
            raise PrometheusQueryError(
                f"Prometheus query failed ({error_type}): {error_message}"
            )

        return payload

    def _extract_results(self, payload: dict) -> list[dict]:
        try:
            results = payload["data"]["result"]
        except (KeyError, TypeError) as error:
            raise PrometheusResponseError(
                "Prometheus response did not contain data.result"
            ) from error

        if not isinstance(results, list):
            raise PrometheusResponseError("Prometheus data.result was not a list")

        return results

    def _to_player_count_sample(self, result: dict) -> PlayerCountSample:
        try:
            metric = result["metric"]
            raw_value = result["value"][1]
        except (KeyError, IndexError, TypeError) as error:
            raise PrometheusResponseError(
                "Prometheus result did not contain metric and value"
            ) from error

        if not isinstance(metric, dict):
            raise PrometheusResponseError("Prometheus result metric was not a dict")

        try:
            player_count = int(float(raw_value))
        except (TypeError, ValueError) as error:
            raise PrometheusResponseError(
                f"Prometheus player count value was not numeric: {raw_value}"
            ) from error

        return PlayerCountSample(metric=metric, player_count=player_count)

    def _escape_label_value(self, value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')
