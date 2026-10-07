import pytest

from main import load_config
from scaler.game_config import GameConfig, load_games


def test_load_games(tmp_path):
    path = tmp_path / "games.toml"
    path.write_text('[games."730"]\nstrategy="aggressive"\n[games."570"]\nstrategy="threshold"\n')
    assert load_games(str(path)) == (
        GameConfig("730", "aggressive"), GameConfig("570", "threshold")
    )


@pytest.mark.parametrize("source", ["cli", "environment"])
def test_load_config_accepts_games_file(tmp_path, monkeypatch, source):
    path = tmp_path / "games.toml"
    path.write_text('[games."730"]\nstrategy="aggressive"\n')
    monkeypatch.delenv("AUTOSCALER_GAMES_CONFIG", raising=False)
    argv = []
    if source == "environment":
        monkeypatch.setenv("AUTOSCALER_GAMES_CONFIG", str(path))
    else:
        monkeypatch.setenv("AUTOSCALER_GAMES_CONFIG", "missing.toml")
        argv = ["--games-config", str(path)]
    assert load_config(argv).games == (GameConfig("730", "aggressive"),)


@pytest.mark.parametrize("contents", [
    "[games]", "", "games = 42", "[games.abc]\nstrategy='threshold'",
    "[games.0]\nstrategy='threshold'", "[games.730]\nstrategy='cost_saving'",
    "[games.730]\nstrategy=42", "[games.730]\nother='threshold'",
    "[games.730]\nstrategy='threshold'\nextra=true", "[games",
])
def test_invalid_games_configuration_fails_at_startup(tmp_path, contents):
    path = tmp_path / "games.toml"
    path.write_text(contents)
    with pytest.raises(SystemExit) as error:
        load_config(["--games-config", str(path)])
    assert error.value.code == 2


def test_missing_games_file_fails_at_startup(tmp_path):
    with pytest.raises(SystemExit) as error:
        load_config(["--games-config", str(tmp_path / "missing.toml")])
    assert error.value.code == 2
