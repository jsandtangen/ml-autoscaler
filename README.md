# ml-autoscaler

Early Python autoscaler prototype for game player counts.

## Local Docker setup

Start Docker Desktop with Linux containers, then run from the repository root:

```powershell
Copy-Item .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose restart prometheus
docker compose logs -f autoscaler
```

Only copy `.env.example` if you do not already have a `.env` file. Compose uses
the example defaults even without `.env`. Shell environment variables override
values in `.env`. The file is ignored by Git.

Restart Prometheus after updating `prometheus/prometheus.yml`: rebuilding the
Python images does not reload configuration in an existing Prometheus container.

This starts the Steam player exporter, Prometheus, Grafana, and the Python loop
using `FakeVMController`. Prometheus is available at http://127.0.0.1:9090, with
scrape status at http://127.0.0.1:9090/targets. Grafana is available at
http://127.0.0.1:3000 with the default local credentials `admin` / `admin`. Its
provisioned `Ruby Acorn Autoscaler` dashboard shows `steam_player_count` and
`running_instances`. Prometheus data is stored in a named Docker volume with
seven-day retention. The autoscaler connects to `http://prometheus:9090` on the
Compose network; `PROMETHEUS_PORT` only changes the host-facing port.

### Player-count source

The Compose stack includes a small Steam exporter. It queries Steam's current
player-count endpoint for `STEAM_APPID` (default `730`) and serves `/metrics` in
Prometheus text format:

```text
# HELP steam_player_count Current Steam player count.
# TYPE steam_player_count gauge
steam_player_count{appid="730"} 150
```

Prometheus scrapes the exporter at `steam-exporter:8000`. Replace that target in
`prometheus/prometheus.yml` if you run another exporter elsewhere. Prometheus
configuration does not interpolate `.env` variables. After changing the scrape
target, run `docker compose restart prometheus`.

Until the exporter is reachable and a sample has been scraped, the loop logs
query warnings and skips scaling. The configured metric and label filters must
match exactly one series; multiple matching series are rejected. No real VMs
are created, and the fake instance count resets when the autoscaler restarts.
The autoscaler also exposes its fake running instance count at
http://127.0.0.1:8001/metrics as a Prometheus gauge named `running_instances`.

Validate Prometheus configuration or run a single evaluation:

```powershell
docker compose run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yml
docker compose run --rm autoscaler python main.py --once
```

Verify the complete running data chain:

```powershell
docker compose ps
docker compose exec -T autoscaler python -c "from urllib.request import urlopen; print(urlopen('http://steam-exporter:8000/metrics', timeout=10).read().decode())"
Invoke-RestMethod "http://127.0.0.1:8001/metrics"
docker compose exec -T autoscaler python main.py --once
docker compose logs --tail 10 autoscaler
```

At http://127.0.0.1:9090/targets, `game_players` and `autoscaler` must be `UP`.
Query `steam_player_count{appid="730"}` and `running_instances` in Prometheus to
check the scraped samples. The autoscaler log must contain `players`,
`desired_instances`, `running_instances`, and `action`; a query warning means no
decision was made. Use your configured app id and host port if you changed the
defaults. The Grafana dashboard is provisioned from
`grafana/provisioning/dashboards/ruby-acorn-dashboard.json`.

Run the exporter directly on the host if you want to inspect its output without
Compose:

```powershell
.\.venv\Scripts\python.exe scripts/steam_player_exporter.py --host 127.0.0.1 --port 8000
```

Stop the services with `docker compose down`. This preserves Prometheus data.
`docker compose down -v` also deletes Prometheus and Grafana data.

## Run the autoscaler loop

Install the package with the development dependencies first:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

For runtime-only installation, use
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.
`requirements.txt` installs the local package; dependencies remain declared in
`pyproject.toml`. Python does not automatically load `.env`; set environment
variables in PowerShell or use the CLI options below when running on the host.

Run one evaluation against local Prometheus:

```powershell
.\.venv\Scripts\python.exe main.py --once --label appid=730
```

Run continuously every 30 seconds:

```powershell
.\.venv\Scripts\python.exe main.py --interval 30 --label appid=730
```

Configuration can also come from environment variables:

```powershell
$env:PROMETHEUS_URL = "http://127.0.0.1:9090"
$env:AUTOSCALER_INTERVAL_SECONDS = "30"
$env:AUTOSCALER_METRIC_NAME = "steam_player_count"
$env:AUTOSCALER_LABELS = "appid=730"
.\.venv\Scripts\python.exe main.py
```

The default strategy is explicitly named `threshold`. Select it with
`--strategy threshold` or `AUTOSCALER_STRATEGY=threshold` (also supported by
Compose). Unknown strategy names are rejected at startup.

Customers who prioritize spare capacity can select `aggressive` with
`--strategy aggressive` or `AUTOSCALER_STRATEGY=aggressive`. It uses the same
thresholds plus one extra VM as a buffer, including at zero players:

| Player count | `threshold` | `aggressive` |
| --- | --- | --- |
| 0-99 | 1 VM | 2 VMs |
| 100-299 | 2 VMs | 3 VMs |
| 300-599 | 3 VMs | 4 VMs |
| 600+ (including 700,000) | 4 VMs | 5 VMs |

These are prototype thresholds, not validated capacity limits. `threshold`
uses fewer VMs; `aggressive` trades that lower cost for extra capacity. Neither
strategy delays scaling or guarantees that the capacity is sufficient.

```powershell
.\.venv\Scripts\python.exe main.py --once --label appid=730 --strategy aggressive
```

The scaling flow is `player_count -> strategy -> desired_instances`, followed by
the decision engine applying the difference through `FakeVMController`.
`ThresholdStrategy` implements the `ScalingStrategy.desired_instances` contract:
below 100 players it requests 1 instance, from 100 to 299 it requests 2, from
300 to 599 it requests 3, and from 600 it requests 4. To add another algorithm,
implement that contract and register its name in `src/scaler/strategies/registry.py`;
the autoscaler loop does not need algorithm-specific changes.

The loop logs and
skips Prometheus/client errors instead of treating missing or invalid metrics as
zero players.

### Strategies per game

Use a TOML configuration to select different strategies for different Steam
games in the same loop. `games.example.toml` contains:

```toml
[games."730"]
strategy = "aggressive"

[games."570"]
strategy = "threshold"
```

Run one evaluation for every configured game:

```powershell
.\.venv\Scripts\python.exe main.py --games-config games.example.toml --once
```

Omit `--once` for continuous operation. Alternatively, set
`AUTOSCALER_GAMES_CONFIG=games.example.toml` when running on the host. Compose
mounts this file read-only at `/app/games.example.toml`; to enable it, set
`AUTOSCALER_GAMES_CONFIG=/app/games.example.toml` in `.env` and run
`docker compose up -d --build autoscaler`. Edit the example file to change the
game assignments, or mount your own file and set its container path.

With a games file, each game has its own strategy, decision engine, and
in-memory fake VM controller. The game configuration overrides the global
strategy and `appid` filter; other label filters (such as `region`) still apply
to every game. Each query must match exactly one series. `--once` evaluates
all configured games once; continuous mode evaluates them sequentially before
waiting for the configured interval. Invalid files or unknown strategies fail
at startup. Configuration changes take effect after restarting the loop.

`running_instances{appid="730"}` and `running_instances{appid="570"}` expose
the separate fake counts. A failed query skips only that game's evaluation,
preserving its existing instance count. The Grafana dashboard shows all player
count series and labels instance series by `appid`.

Prometheus must have a player-count source for **each** configured game. The
default Compose exporter still provides only `STEAM_APPID` (default `730`);
`570` requires a separate exporter and Prometheus scrape target. Adding a game
to this file does not add a player-count source. Without a games file, the
existing single-game CLI and unlabeled `running_instances` metric still work.
Available strategy names are `threshold` and `aggressive`; `cost_saving` is not
implemented.
