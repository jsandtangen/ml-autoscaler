# ml-autoscaler

Early Python autoscaler prototype for game player counts.

## Local Docker setup

Start Docker Desktop with Linux containers, then run from the repository root:

```powershell
Copy-Item .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose logs -f autoscaler
```

Only copy `.env.example` if you do not already have a `.env` file. Compose uses
the example defaults even without `.env`. Shell environment variables override
values in `.env`. The file is ignored by Git.

This starts Prometheus and the Python loop using `FakeVMController`. Prometheus
is available at http://127.0.0.1:9090, with scrape status at
http://127.0.0.1:9090/targets. Its data is stored in a named Docker volume with
seven-day retention. The autoscaler connects to `http://prometheus:9090` on the
Compose network; `PROMETHEUS_PORT` only changes the host-facing port.

### Player-count source

An exporter is **not included**. The `game_players` job in
`prometheus/prometheus.yml` expects an HTTP exporter on the host at port 8000,
serving `/metrics` in Prometheus text format, for example:

```text
# HELP steam_player_count Current game player count.
# TYPE steam_player_count gauge
steam_player_count{appid="730"} 150
```

The exporter must be reachable from Docker (listen on a suitable interface,
typically `0.0.0.0`, rather than only loopback). Replace
`host.docker.internal:8000` with your actual exporter address if needed.
Prometheus configuration does not interpolate `.env` variables. After changing
the scrape target, run `docker compose restart prometheus`.

Until the exporter is reachable and a sample has been scraped, the loop logs
query warnings and skips scaling. The configured metric and label filters must
match exactly one series; multiple matching series are rejected. No real VMs
are created, and the fake instance count resets when the autoscaler restarts.

Validate Prometheus configuration or run a single evaluation:

```powershell
docker compose run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yml
docker compose run --rm autoscaler python main.py --once
```

Stop the services with `docker compose down`. This preserves Prometheus data.
`docker compose down -v` also deletes that data.

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

The current loop uses `ThresholdStrategy` and `FakeVMController`. It logs and
skips Prometheus/client errors instead of treating missing or invalid metrics as
zero players.
