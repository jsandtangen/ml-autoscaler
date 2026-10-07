from dataclasses import dataclass
from pathlib import Path
import tomllib

from scaler.strategies.registry import STRATEGIES


@dataclass(frozen=True)
class GameConfig:
    appid: str
    strategy_name: str


def load_games(path: str) -> tuple[GameConfig, ...]:
    with Path(path).open("rb") as source:
        config = tomllib.load(source)

    if set(config) != {"games"} or not isinstance(config["games"], dict):
        raise ValueError("Game configuration must contain only a games table")
    if not config["games"]:
        raise ValueError("At least one game must be configured")

    games = []
    for appid, settings in config["games"].items():
        if not appid.isascii() or not appid.isdecimal() or int(appid) <= 0:
            raise ValueError(f"Invalid Steam appid: {appid}")
        if not isinstance(settings, dict) or set(settings) != {"strategy"}:
            raise ValueError(f"Game {appid} must specify exactly one strategy")
        strategy = settings["strategy"]
        if not isinstance(strategy, str) or strategy not in STRATEGIES:
            raise ValueError(f"Unknown scaling strategy for game {appid}: {strategy}")
        games.append(GameConfig(appid, strategy))
    return tuple(games)
