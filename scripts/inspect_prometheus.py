from scaler.data.client import GameDataClient

client = GameDataClient("http://127.0.0.1:9090")

player_count = client.get_player_count({"appid": "730"})

print(player_count)