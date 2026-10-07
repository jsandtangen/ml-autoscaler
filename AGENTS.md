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
scaling decision through a controller. The current implementation uses a fake,
in-memory controller; it does not provision real VMs. Although the README names
the project `ml-autoscaler`, there is no implemented ML strategy yet.

Repository facts checked on 2026-10-07:

- Distribution name: `ruby-acorn`, version `0.1.0`.
- Import package: `scaler`, located under `src/`. Use this name for imports.
- Python requirement: `>=3.11`; build backend: setuptools.
- Runtime dependencies include `requests`; tests use pytest via the `dev` extra.
- `main.py` contains a runnable autoscaler loop using `GameDataClient`,
  `ThresholdStrategy`, `DecisionEngine`, and `FakeVMController`.
- `requirements.txt` installs the local package using `pyproject.toml` metadata.
- `Dockerfile` builds the loop image; Compose runs it alongside Prometheus.
- `.env.example` documents Compose defaults; `.env` is ignored by Git.
- Prometheus scrapes itself and expects an external player-count exporter at
  `host.docker.internal:8000/metrics`. No exporter or real VM provider exists.
- README.md documents local Docker setup, the required metric source, and the loop.

Verify these facts against the current files before relying on them. Proposed
work below is guidance, not a requirement to expand every task's scope.

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/scaler/data/client.py` | Prometheus HTTP queries, response parsing, player count samples and exceptions |
| `src/scaler/strategies/base.py` | Abstract `ScalingStrategy` contract |
| `src/scaler/strategies/threshold.py` | Current threshold-based instance calculation |
| `src/scaler/engine/decision_engine.py` | Compare desired/current counts and apply the difference |
| `src/scaler/infrastructure/vm_controller.py` | In-memory `FakeVMController` |
| `main.py` | Runnable autoscaler loop configured by CLI flags and environment variables |
| `scripts/inspect_prometheus.py` | Manual player-count query against local Prometheus |
| `tests/` | Unit tests for the client, strategy, decision engine, and fake controller |
| `pyproject.toml` | Packaging, Python requirement, dev dependencies, and pytest configuration |
| `prometheus/prometheus.yml` | Self-scrape and external player-count exporter scrape configuration |
| `docker-compose.yml` | Local Prometheus and autoscaler services with persistent metric storage |
| `Dockerfile` | Non-root Python autoscaler image |
| `.env.example` | Compose configuration defaults |
| `requirements.txt` | Runtime installation of the local package |

## Behavior and contracts

- `ScalingStrategy.desired_instances(player_count)` returns the desired count.
- `ThresholdStrategy` returns 1 below 100 players, 2 from 100 to 299, 3 from
  300 to 599, and 4 at 600 or above. These are prototype values, not validated
  production capacity limits.
- `DecisionEngine.evaluate(player_count)` reads the current count, scales up or
  down by the difference, and returns the resulting running count. Equal counts
  require no scaling action.
- Controllers expose `running_instances()`, `scale_up(count)`, and
  `scale_down(count)`. The fake starts at zero and clamps scale-down at zero.
- `GameDataClient` defaults to metric `steam_player_count` and a five-second
  HTTP timeout. It queries `<prometheus_url>/api/v1/query` using `requests`.
- `get_player_counts(label_filters)` returns `PlayerCountSample` objects with
  metric labels and integer counts. Label filters are sorted and values escaped.
- `get_player_count(label_filters)` requires exactly one matching series; it
  does not sum multiple series. Empty results raise `PlayerCountNotFoundError`.
- Client errors derive from `GameDataClientError`: `PrometheusQueryError` for
  request/query failures and `PrometheusResponseError` for malformed responses.
- The inspection script uses `http://127.0.0.1:9090` and `appid="730"`. These are
  script defaults, not a configured environment. No exporter is supplied here.
- The autoscaler loop in `main.py` defaults to `http://127.0.0.1:9090`,
  `steam_player_count`, and a 30-second interval. It accepts `--label KEY=VALUE`
  filters and `--once` for a single evaluation. Environment alternatives are
  `PROMETHEUS_URL`, `AUTOSCALER_INTERVAL_SECONDS`, `AUTOSCALER_METRIC_NAME`, and
  `AUTOSCALER_LABELS=key=value,key2=value2`.
- Prometheus/client errors in the loop are logged and skipped; failed queries are
  not interpreted as zero players.

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
docker compose logs -f autoscaler
docker compose down
```

Optionally copy `.env.example` to `.env` if no `.env` exists. Compose reads it;
Python running directly on the host does not. In containers, use
`http://prometheus:9090`; on the host, use `http://127.0.0.1:9090` (or the
configured host port). Shell environment variables override Compose `.env`.
Prometheus YAML does not expand those variables. The external exporter must
be reachable from Docker and expose the configured metric and labels.

Validate Prometheus configuration with:

```powershell
docker compose run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yml
```

Prometheus data persists in `prometheus_data`; `docker compose down -v` deletes
it. The fake controller's count resets on autoscaler restart. Initial scrape or
connection failures are handled by the loop's existing log-and-skip behavior.

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

The next useful milestone is connecting a real metric source to the local setup:

1. Provide an exporter that exposes `steam_player_count` and configure its target
   in `prometheus/prometheus.yml`.
2. Verify the Compose stack end to end with that exporter and Docker running.
3. Decide whether the loop should keep running after repeated metric failures,
   back off, or alert.
4. Keep real VM control, ML strategies, cooldown/hysteresis, and production
   deployment as future design work unless explicitly requested.

Real VM control, ML strategies, cooldown/hysteresis, and production deployment
remain future design work unless explicitly requested.
