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
- Tests use pytest; the `dev` extra declares `pytest>=8`.
- `requests` is imported by the data client but is not declared as a dependency.
- `main.py`, `requirements.txt`, `docker-compose.yml`, `.env.example`, and
  `prometheus/prometheus.yml` are empty placeholders.
- README.md contains only a project title. No complete application entry point,
  autoscaling loop, exporter, deployment setup, or real VM provider exists.

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
| `scripts/inspect_prometheus.py` | Manual player-count query against local Prometheus |
| `tests/` | Unit tests for the client, strategy, decision engine, and fake controller |
| `pyproject.toml` | Packaging, Python requirement, dev dependencies, and pytest configuration |
| `prometheus/prometheus.yml` | Placeholder for Prometheus configuration |
| `docker-compose.yml` | Placeholder for local service orchestration |

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

If the Windows launcher is unavailable or another supported version is installed,
use that interpreter instead. Then install the package, test extra, and the
currently undeclared runtime dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" requests
.\.venv\Scripts\python.exe -m pytest -q
```

The separate `requests` installation is a temporary workaround. When fixing
packaging, declare it in `pyproject.toml` so normal package installation supplies
runtime dependencies. Do not treat the empty `requirements.txt` as authoritative.

With a reachable Prometheus instance exposing the expected metric:

```powershell
.\.venv\Scripts\python.exe scripts/inspect_prometheus.py
```

Editable installation makes `scaler` importable with the src layout. Prefer it
over adding sys.path hacks. No supported application-start or Docker command is
available until the placeholders are implemented.

Earlier environment checks found a virtual environment referencing a missing
Python installation, so tests could not run. Recheck the environment on each
session; do not assume that failure still applies or claim tests passed without
running them. Do not delete an existing virtual environment without checking it.

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
- `.gitignore` excludes `.venv/`, Python caches, pytest caches, and
  `personal_notes.md`. Do not rely on private notes being present on other machines.
- Keep secrets out of source, logs, tests, and examples. Use placeholders when
  adding environment configuration.

## Suggested next milestone

The next useful milestone is a runnable demonstration connecting the existing
components:

1. Establish a working Python environment, declare runtime dependencies, and
   run the existing test suite.
2. Add an entry point that reads configuration, obtains one player count,
   evaluates it through `DecisionEngine` with `ThresholdStrategy` and
   `FakeVMController`, and logs the decision at a configurable interval.
3. Define behavior for unavailable/invalid metrics before enabling the loop;
   do not interpret a failed query as zero players.
4. Document setup and operation in README.md and fill service configuration
   based on the actual chosen metric source.

Real VM control, ML strategies, cooldown/hysteresis, and production deployment
remain future design work unless explicitly requested.
