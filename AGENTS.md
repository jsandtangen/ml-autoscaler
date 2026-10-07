# Agent Guide

## Scope and collaboration

This guide applies to the entire repository. Follow more specific AGENTS.md
files if they are added in subdirectories. Explicit user instructions take
precedence over this guide.

- Communicate with the user in Norwegian unless they request another language.
- Read the affected code and tests before changing behavior.
- Keep changes focused on the requested task and follow existing patterns.
- Preserve existing user changes. Do not reset, revert, or delete unrelated work.
- For questions about the project or next steps, inspect and advise; do not
  implement a proposed feature unless the user requests implementation.
- Report what changed, how it was verified, and any remaining blockers.
- Update this guide when architecture, commands, or project status change.
  Never record credentials, tokens, or other secrets here.

## Project purpose and current status

Ruby Acorn is an early Python autoscaler prototype. It reads game player counts
from Prometheus, calculates a desired number of VM instances, and applies a
scaling decision through a controller. The default is a fake in-memory controller;
an optional host Docker controller starts/stops bounded local containers, not
full virtual machines. Although the README names
the project `ml-autoscaler`, there is no implemented ML strategy yet.

Repository facts checked on 2026-10-07:

- Distribution name: `ruby-acorn`, version `0.1.0`.
- Import package: `scaler`, located under `src/`. Use this name for imports.
- Python requirement: `>=3.11`; build backend: setuptools.
- Runtime dependencies include `requests`; tests use pytest via the `dev` extra.
- `main.py` contains a runnable autoscaler loop using `GameDataClient`,
  `ThresholdStrategy`, `DecisionEngine`, and `FakeVMController`.
- `requirements.txt` installs the local package using `pyproject.toml` metadata.
- `Dockerfile` builds the loop/exporter image; Compose runs the Steam exporter,
  Prometheus, Grafana, and autoscaler.
- `.env.example` documents Compose defaults; `.env` is ignored by Git.
- Prometheus scrapes itself and the included Steam exporter at
  `steam-exporter:8000/metrics`, plus autoscaler metrics at
  `autoscaler:8001/metrics`. No real VM provider exists.
- Grafana is provisioned with a Prometheus datasource and a `Ruby Acorn
  Autoscaler` dashboard for source counts and autoscaler decision metrics.
- `Ruby Acorn Scaling` is the second dashboard, provisioned from
  `grafana/provisioning/dashboards/ruby-acorn-scaling-dashboard.json`. It has an
  appid selector, decision input, desired/running counts, strategy, event counts,
  and decision age. Links connect both dashboards.
- `Ruby Acorn Customer Costs` is the third dashboard, provisioned from
  `grafana/provisioning/dashboards/ruby-acorn-customer-dashboard.json`. It has a
  game selector, aggregate VM count and EUR/hour cost/baseline/savings, per-game
  trends, strategy information, and a below-strategy-target capacity indicator.
  It does not estimate real player capacity or cumulative spending. Links connect
  all three dashboards, with game selection shared by scaling/customer views.
- README.md documents local Docker setup, the required metric source, Grafana,
  and the loop.

Verify these facts against the current files before relying on them. Proposed
work below is guidance, not a requirement to expand every task's scope.

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/scaler/data/client.py` | Prometheus HTTP queries, response parsing, player count samples and exceptions |
| `src/scaler/data/history.py` | Append-only CSV player-count history |
| `src/scaler/forecasting.py` | Persistence/moving-average forecasts and rolling MAE/RMSE evaluation |
| `scripts/evaluate_forecasts.py` | Offline baseline comparison from player history, with JSON output |
| `src/scaler/strategies/base.py` | Abstract `ScalingStrategy` contract |
| `src/scaler/strategies/threshold.py` | Current threshold-based instance calculation |
| `src/scaler/strategies/aggressive.py` | Threshold baseline plus one buffer instance |
| `src/scaler/engine/decision_engine.py` | Compare desired/current counts and apply the difference |
| `src/scaler/infrastructure/vm_controller.py` | In-memory `FakeVMController` |
| `src/scaler/infrastructure/docker_controller.py` | Label-scoped local Docker resource controller |
| `scripts/demo_docker_scaling.py` | Reproducible real-container demo with cleanup and JSON evidence |
| `main.py` | Runnable autoscaler loop configured by CLI flags and environment variables |
| `src/scaler/game_config.py` | Validated per-game TOML configuration |
| `src/scaler/cost.py` | Simple hourly VM cost and fixed-baseline comparison |
| `games.example.toml` | Example mapping of Steam appids to strategies |
| `src/scaler/exporter/steam_player_exporter.py` | Steam current-player client and Prometheus text exporter |
| `src/scaler/exporter/autoscaler_metrics.py` | Prometheus text exporter for autoscaler decision snapshots |
| `scripts/steam_player_exporter.py` | Script entrypoint for the Steam player exporter |
| `scripts/inspect_prometheus.py` | Manual player-count query against local Prometheus |
| `tests/` | Unit tests for the client, strategy, decision engine, and fake controller |
| `pyproject.toml` | Packaging, Python requirement, dev dependencies, and pytest configuration |
| `prometheus/prometheus.yml` | Self-scrape and external player-count exporter scrape configuration |
| `grafana/provisioning/` | Grafana datasource and dashboard provisioning |
| `docker-compose.yml` | Local Prometheus, Grafana, exporter, and autoscaler services with persistent metric storage |
| `Dockerfile` | Non-root Python autoscaler image |
| `.env.example` | Compose configuration defaults |
| `requirements.txt` | Runtime installation of the local package |

## Behavior and contracts

- `ScalingStrategy.desired_instances(player_count)` returns the desired count.
- Forecasting is independent of scaling in `scaler.forecasting`. Forecasters
  implement `predict(player_counts)` on a past-only input window. Persistence
  returns its latest count; moving average returns its mean. Offline evaluation
  uses identical rolling windows and future targets, reporting MAE/RMSE per game.
  Window/horizon units are observations, not elapsed time; irregular sampling is
  not resampled. CSV loading sorts each appid separately and rejects malformed
  rows and duplicate timestamps. No ML model or predictive scaling is enabled.
  Run `.\.venv\Scripts\python.exe scripts/evaluate_forecasts.py --appid 730
  --window-size 10 --horizon 1` against default `data/player_history.csv`, or
  supply `--history-path`. At least window size + horizon observations are needed
  per evaluated game. Later models must be compared on the same chronological
  evaluation period and future targets.
- Strategies are selected by `--strategy` or `AUTOSCALER_STRATEGY`, defaulting
  to `threshold`. `src/scaler/strategies/registry.py` maps names to implementations;
  unknown names fail at startup. Add algorithms there without changing the loop.
- Each evaluation computes the desired count once; `DecisionEngine` applies it
  via `apply_desired_instances(desired)`. `evaluate(player_count)` remains available.
- `aggressive` selects `AggressiveStrategy`: the threshold baseline plus one
  buffer instance (2 at zero players, 5 at 600 or above). It prioritizes spare
  capacity over cost, with no delay or hysteresis. `threshold` remains the default.
- `--games-config` or `AUTOSCALER_GAMES_CONFIG` loads a TOML file with
  `[games."APPID"]` tables containing `strategy`. Each game gets a separate
  strategy, decision engine, and selected controller. Its appid and strategy override
  global defaults; other label filters apply to all games. Invalid configuration
  fails at startup. Changes require restarting the loop.
- In per-game mode, each cycle evaluates all configured games sequentially;
  `--once` processes all once. Query failures skip only the affected game and
  preserve its count. Metrics are `running_instances{appid="..."}`; legacy
  single-game mode retains the unlabeled metric. Grafana shows all player series.
- Compose optionally loads the mounted `/app/games.example.toml` via
  `AUTOSCALER_GAMES_CONFIG`. The default exporter still serves only `STEAM_APPID`;
  additional games require their own exporter/scrape source. The example uses
  `730: aggressive` and `570: threshold`; no `cost_saving` strategy exists.
- `ThresholdStrategy` returns 1 below 100 players, 2 from 100 to 299, 3 from
  300 to 599, and 4 at 600 or above. These are prototype values, not validated
  production capacity limits.
- `DecisionEngine.evaluate(player_count)` reads the current count, scales up or
  down by the difference, and returns the resulting running count. Equal counts
  require no scaling action.
- Controllers expose `running_instances()`, `scale_up(count)`, and
  `scale_down(count)`. The fake starts at zero and clamps scale-down at zero.
- `--controller` / `AUTOSCALER_CONTROLLER` selects `fake` (default) or `docker`.
  Docker mode runs on the host with the Docker CLI, not inside the current Compose
  image. `--docker-namespace` / `AUTOSCALER_DOCKER_NAMESPACE` scopes ownership;
  each game has its own game label. One manager process must own each namespace.
- Docker's namespace-wide limit is 1-5 containers via `--docker-max-instances` /
  `AUTOSCALER_DOCKER_MAX_INSTANCES`, default 5. Limit breaches raise ControllerError;
  desired counts are not silently clamped. Only managed/namespace/game-labeled
  resources are counted or stopped. Containers use alpine:3.22, 32 MiB, 0.1 CPU,
  32 PIDs, non-root, no network/host mounts, read-only root, and auto-removal.
- Normal host loop exits leave Docker resources running; controllers rediscover
  them on restart. `DockerController.cleanup()` explicitly removes only its scope.
  The demo script uses a unique namespace, always attempts cleanup, and records
  real container IDs in optional JSON reports under ignored `demo-results/`.
- Infrastructure failures do not record successful decisions/events. The loop
  refreshes actual counts after partial failures when possible. `--once` returns
  failure on controller errors; continuous mode logs and retries next cycle.
- `GameDataClient` defaults to metric `steam_player_count` and a five-second
  HTTP timeout. It queries `<prometheus_url>/api/v1/query` using `requests`.
- `get_player_counts(label_filters)` returns `PlayerCountSample` objects with
  metric labels and integer counts. Label filters are sorted and values escaped.
- `get_player_count(label_filters)` requires exactly one matching series; it
  does not sum multiple series. Empty results raise `PlayerCountNotFoundError`.
- Client errors derive from `GameDataClientError`: `PrometheusQueryError` for
  request/query failures and `PrometheusResponseError` for malformed responses.
- The inspection script uses `http://127.0.0.1:9090` and `appid="730"`. These are
  script defaults; Compose supplies the included Steam exporter.
- The autoscaler loop in `main.py` defaults to `http://127.0.0.1:9090`,
  `steam_player_count`, and a 30-second interval. It accepts `--label KEY=VALUE`
  filters and `--once` for a single evaluation. Environment alternatives are
  `PROMETHEUS_URL`, `AUTOSCALER_INTERVAL_SECONDS`, `AUTOSCALER_METRIC_NAME`, and
  `AUTOSCALER_LABELS=key=value,key2=value2`.
- Continuous autoscaler runs expose decision metrics on `/metrics`, using
  `AUTOSCALER_METRICS_HOST` and `AUTOSCALER_METRICS_PORT` for the bind address.
- `--player-history-path` / `AUTOSCALER_PLAYER_HISTORY_PATH` appends successful
  player-count observations to CSV as `timestamp,appid,player_count`. Compose
  defaults this to `/app/data/player_history.csv`, mounted from ignored local
  `./data/`. Failed queries are not recorded as zero. This is raw history for
  future predictive input-window training, not a scaling strategy.
- Decision gauges are `player_count`, `running_instances`, `desired_instances`,
  `scaling_action` (-1 down, 0 unchanged, 1 up), `strategy{name="..."}` (value 1),
  and `last_decision_timestamp_seconds`. Per-game mode adds appid to all of them;
  legacy mode omits it. Registered strategies use their registry name; injected
  unregistered ones use their class name.
- Strategy info and running count are available before the first evaluation.
  Other metrics appear only after success and are updated as one snapshot after
  scaling. Failed queries preserve the snapshot and timestamp. Action is the
  latest decision, not a counter. Grafana displays input versus source counts,
  desired versus running instances, action, strategy, and decision age.
- `promtool check metrics` flags the requested `player_count` gauge name because
  `_count` is normally reserved for histogram/summary counts. Prometheus accepts
  the metric; distinguish this naming lint warning from a text-format error.
- `scale_up_events_total` and `scale_down_events_total` are per-game counters
  (unlabeled in legacy mode). They count successful scaling operations, not VMs;
  unchanged decisions and failures do not increment them. Counters reset on
  process restart. The scaling dashboard uses increase over rolling intervals
  and raw totals; it does not provide exact event timestamps or capture an
  increase that happened before the first scrape.
- Cost gauges `dynamic_cost`, `fixed_baseline_cost`, and `savings` are EUR per
  hour at the current running allocation, not cumulative spending. Defaults:
  0.10 EUR per VM-hour and 4 fixed VMs per game. Savings = fixed minus dynamic;
  negative savings are retained. `CostModel` is independent of scaling strategy.
- Configure price and baseline via `--vm-cost-per-hour` /
  `AUTOSCALER_VM_COST_PER_HOUR` and `--fixed-baseline-instances` /
  `AUTOSCALER_FIXED_BASELINE_INSTANCES`. Values must be nonnegative, price finite,
  baseline integer. Assumptions apply separately to each game; per-game mode
  adds appid to cost metrics. The scaling dashboard displays all three costs.
- Prometheus/client errors in the loop are logged and skipped; failed queries are
  not interpreted as zero players.
- `SteamPlayerClient` queries Steam's current-player endpoint with an `appid` and
  exposes the count as `steam_player_count{appid="..."}` through `/metrics`.
  Exporter fetch/response errors return HTTP 503 and are not emitted as zero.

Keep metrics retrieval, scaling policy, orchestration, and infrastructure control
separate. New strategies should implement the existing strategy contract;
provider integrations should fit the controller interface. Do not silently
change thresholds, series aggregation, or error behavior.

## Setup and commands

Run commands from the repository root. The workspace currently uses Windows
PowerShell. The paths may contain spaces; quote absolute paths when necessary.

Create a virtual environment with an available Python 3.11+ interpreter:

```powershell
py -3.11 -m venv .venv
```

If the Windows launcher is unavailable, use another supported interpreter or
`uv venv .venv --python 3.11`. Then install the package and test extra:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

`requirements.txt` installs the local package; dependency declarations remain
in `pyproject.toml`.

With a reachable Prometheus instance exposing the expected metric:

```powershell
.\.venv\Scripts\python.exe scripts/inspect_prometheus.py
```

Run the Steam player exporter directly on the host:

```powershell
.\.venv\Scripts\python.exe scripts/steam_player_exporter.py --host 127.0.0.1 --port 8000
```

Run one autoscaler evaluation:

```powershell
.\.venv\Scripts\python.exe main.py --once --label appid=730
```

Run the continuous prototype loop:

```powershell
.\.venv\Scripts\python.exe main.py --interval 30 --label appid=730
```

Editable installation makes `scaler` importable with the src layout. Prefer it
over adding sys.path hacks.

With Docker Desktop running Linux containers:

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose restart prometheus
docker compose logs -f autoscaler
docker compose down
```

Optionally copy `.env.example` to `.env` if no `.env` exists. Compose reads it;
Python running directly on the host does not. In containers, use
`http://prometheus:9090`; on the host, use `http://127.0.0.1:9090` (or the
configured host port). Shell environment variables override Compose `.env`.
Prometheus YAML does not expand those variables. The included exporter uses
`STEAM_APPID`, `STEAM_EXPORTER_METRIC_NAME`, and
`STEAM_REQUEST_TIMEOUT_SECONDS`.

Validate Prometheus configuration with:

```powershell
docker compose run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yml
```

Prometheus data persists in `prometheus_data`; Grafana data persists in
`grafana_data`; `docker compose down -v` deletes both. The fake controller's
count resets on autoscaler restart. Initial scrape or connection failures are
handled by the loop's existing log-and-skip behavior.
Restart Prometheus after changing its YAML: `compose up --build` does not reload
configuration in an unchanged, already running Prometheus container.

On 2026-10-07, the live Compose data chain was verified: the Steam exporter
returned `steam_player_count{appid="730"}`, the `game_players` target was `up`,
and an evaluation logged 724491 players and scaling from zero to four fake
instances. The previous running stack lacked the exporter and needed a
Prometheus restart to load the updated target. Recheck live status each session.

On 2026-10-07, the observability chain was extended and verified live: the
autoscaler exposed `running_instances` on port 8001, Prometheus returned
`steam_player_count{appid="730"} = 730974` and `running_instances = 4`, and
Grafana provisioned the `Ruby Acorn Autoscaler` dashboard. Recheck live values
each session because Steam player counts and fake instance state change.

On 2026-10-07, the broken `.venv` referenced a missing Python 3.11.1
installation and was recreated with Python 3.11.13. Recheck the environment on
each session; do not assume that failure still applies or claim tests passed
without running them. Do not delete an existing virtual environment without
checking it.

## Verification and implementation practices

- Use `pyproject.toml` as the primary packaging configuration.
- Keep Python code consistent with nearby code; add abstractions only when useful.
- Use injected fake sessions/controllers for unit tests. The existing client
  tests show the fake HTTP response/session pattern.
- Unit tests must not depend on live Prometheus, external network access, cloud
  credentials, or real VM provisioning.
- For behavioral changes, test relevant boundaries and failure cases. Strategy
  changes should cover exact thresholds; engine changes should cover scaling up,
  down, and unchanged counts; client changes should cover response/error handling.
- Run focused tests while developing and the full pytest suite when appropriate.
  Documentation-only changes need review and a diff check, not a live service.
- No formatter, linter, type checker, or CI workflow is currently configured.
  Do not report checks that are not present or were not run.
- `.gitignore` excludes `.venv/`, Python caches, pytest caches, egg-info
  metadata, and `personal_notes.md`. Do not rely on private notes being present
  on other machines.
- Keep secrets out of source, logs, tests, and examples. Use placeholders when
  adding environment configuration.

## Suggested next milestone

The Compose data chain has been verified live. Repeat these checks after changes:

1. Start the exporter, Prometheus, and autoscaler with `docker compose up -d --build`.
2. Confirm that Prometheus has scraped `steam_player_count{appid="730"}`.
3. Run one autoscaler evaluation against the scraped metric.
4. Decide whether the loop should keep running after repeated metric failures,
   back off, or alert.
5. Keep real VM control, ML strategies, cooldown/hysteresis, and production
   deployment as future design work unless explicitly requested.

Real VM control, ML strategies, cooldown/hysteresis, and production deployment
remain future design work unless explicitly requested.
