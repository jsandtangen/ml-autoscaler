import requests


class GameDataClient:
    def __init__(self, prometheus_url):
        self.prometheus_url = prometheus_url.rstrip("/")

    def get_player_count(self, appid):
        query = f'steam_player_count{{appid="{appid}"}}'

        response = requests.get(
            f"{self.prometheus_url}/api/v1/query",
            params={"query": query},
            timeout=5,
        )
        response.raise_for_status()

        data = response.json()
        results = data["data"]["result"]

        if not results:
            raise ValueError(f"No player data found for appid {appid}")

        return int(float(results[0]["value"][1]))