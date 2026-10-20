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
the autoscaler's decision metrics. Prometheus data is stored in a named Docker volume with
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
Compose also writes successful player-count observations to
`./data/player_history.csv` as append-only CSV with
`timestamp,appid,player_count`. This is the raw historical dataset intended for
future predictive models that need input windows instead of only the latest
Prometheus value.

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

### Historical player data

A predictive model needs historical samples, not only the latest value returned
by Prometheus. Enable CSV history with `--player-history-path` or
`AUTOSCALER_PLAYER_HISTORY_PATH`:

```powershell
.\.venv\Scripts\python.exe main.py --label appid=730 --player-history-path data/player_history.csv
```

Each successful Prometheus query appends one row:

```csv
timestamp,appid,player_count
1760000000.123,730,700000
```

Failed queries are skipped and are not recorded as zero. In per-game mode, the
`appid` column distinguishes games. Compose enables this by default at
`/app/data/player_history.csv`, mounted to `./data/player_history.csv` on the
host. The `data/` directory is ignored by Git because it is generated training
data.

### Forecasting baselines

Compare two simple forecasts before introducing TimesFM: `persistence` predicts
the latest player count, and `moving_average` predicts the mean of the input
window. Evaluate them on the recorded history:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_forecasts.py --history-path data/player_history.csv --appid 730 --window-size 10 --horizon 1
```

The JSON report contains the evaluated sample count, MAE and RMSE in players
(lower is better), and a `latest_prediction` using the newest input window for
the selected horizon. Both baselines use identical rolling windows and future
targets; target values are never included in prediction inputs. Omit `--appid`
to evaluate every game separately. Empty appids represent legacy history.

Window size and horizon count observations, not seconds. At a regular 30-second
sampling interval, horizon 10 corresponds to approximately five minutes; failed
queries can lengthen that interval. Each game needs at least `window_size +
horizon` observations. Invalid rows and duplicate timestamps fail evaluation.
These are offline comparison results over available history. A later ML model
should use the same held-out chronological period, windows and horizon for a
fair comparison. Forecasting lives separately from the scaling strategies.

### TimesFM evaluation

The optional ML extra adds pretrained **TimesFM 2.5 (200M, PyTorch)** through
`timesfm==2.0.2`. This first integration uses zero-shot inference: our recorded
player counts are the model's input context, not a dataset used to train its
weights. Fine-tuning and offline predictive scaling are separate experiments
described below. The running autoscaler still uses its configured rule strategy.
The adapter follows the [official TimesFM API](https://pypi.org/project/timesfm/2.0.2/).

Install and compare all three forecasts on the same rolling windows and targets:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ml]"
.\.venv\Scripts\python.exe scripts/evaluate_forecasts.py --include-timesfm --appid 730 --window-size 512 --horizon 10
```

Use a working Python 3.11+ environment. In the current workspace, `.venv-ml`
was created separately with Python 3.12.11 because the old `.venv` cannot start;
replace `.venv` in these commands with `.venv-ml` to use it. The old environment
was preserved.

The first TimesFM prediction downloads `google/timesfm-2.5-200m-pytorch` weights
from Hugging Face to ignored `.model-cache/`; installation and the initial
download require internet access and substantial disk space. Override the cache
with `--model-cache-dir PATH`. After downloading, `--local-files-only` prevents
checkpoint downloads. Baseline-only evaluation needs neither ML dependencies
nor model weights. The model is loaded once and reused across windows/games;
PyTorch graph compilation is disabled for this host integration.

For horizon 10 the adapter returns the **tenth future prediction**, matching
the evaluator's target. The JSON report includes the checkpoint ID and the
`pretrained_zero_shot` mode. Windows and horizon still count observations;
timestamps are not model features and missing intervals are not resampled.
Inspect sampling gaps before interpreting forecasts as minutes into the future.
The example needs at least 522 observations per selected game; larger context
does not by itself establish sufficient evaluation coverage. Choose window and
horizon before comparing models and keep the final chronological test period
separate from model/parameter selection.

Unit tests inject a fake model and never download weights. A successful real
model smoke check on synthetic input verifies inference only, not accuracy on
game data. Actual forecast quality must be measured on collected player history.

### Historical source snapshot (2026-10-20)

A local [SteamCharts](https://steamcharts.com/app/730) snapshot is available under
ignored `data/external/steamcharts/`. SteamCharts describes its collection as
[hourly concurrent-player observations from the Steam Web API](https://steamcharts.com/about).
The downloaded `730-chart-data.json` mixes older coarse aggregates and recent
hourly observations. Only its consecutive approximately hourly suffix was used:

- `730-hourly.csv`: 720 observations, September 20 to October 20, 2026 (UTC).
- `730-hourly-after-zero.csv`: recommended initial subset, 444 observations,
  October 1 at 19:00 to October 20 at 06:00 (UTC).
- `730-hourly.metadata.json`: source URLs, hashes, preparation and quality flags.

Hourly timestamps were rounded to the nearest UTC hour (maximum shift 207
seconds), with exactly one observed count per hour and no missing/duplicate hour
slots. Original timestamps are preserved in the raw JSON. No counts were
interpolated or synthesized. The full series contains a suspicious zero at
October 1, 18:00 UTC, between counts above one million. Its cause is unverified;
the recommended subset starts after it instead of treating it as known demand
or inventing a replacement.

Run the comparison with one week of context and a one-hour forecast horizon:

```powershell
.\.venv-ml\Scripts\python.exe scripts/evaluate_forecasts.py --history-path data/external/steamcharts/730-hourly-after-zero.csv --appid 730 --window-size 168 --horizon 1 --include-timesfm --local-files-only
```

This provides 276 rolling evaluation targets. These hourly data cannot validate
forecasts five minutes ahead. They are local third-party source snapshots, not
an openly licensed dataset for redistribution. Keep source attribution and
check permissions before publishing the raw dataset. Baselines and one real
TimesFM forecast have been verified on this subset; a complete TimesFM score
comparison has not yet been run. Snapshot selection followed a visible quality
flag, so results are exploratory and should later be checked on newly collected
data.

A separate raw local Prometheus snapshot is retained under
`data/external/prometheus/730-last-7-days.json`: 438 original scrape observations
with a gap of about 3.87 days. It is not merged into the hourly series. These
imported files do not populate the live loop's `data/player_history.csv`.

### Fine-tune TimesFM on player history

`scripts/finetune_timesfm.py` trains a LoRA adapter with the Transformers
TimesFM 2.5 checkpoint, following the
[official fine-tuning workflow](https://github.com/google-research/timesfm/tree/master/timesfm-forecasting/examples/finetuning).
Install the optional training dependencies and run a new experiment:

```powershell
.\.venv-ml\Scripts\python.exe -m pip install -e ".[dev,ml,training]"
.\.venv-ml\Scripts\python.exe scripts/finetune_timesfm.py --history-path data/external/steamcharts/730-hourly-after-zero.csv --appid 730 --output-dir model-runs/my-timesfm-run --epochs 3
```

The first run downloads `google/timesfm-2.5-200m-transformers` to `.model-cache/`.
This is a different checkpoint format from the inference-only PyTorch adapter.
Use `--local-files-only` once the Transformers checkpoint is cached.
Training uses CPU when CUDA is unavailable. Defaults are float32, four CPU
threads, batch size 4, learning rate 0.00005 and seed 42. LoRA uses rank 4,
alpha 8 and dropout 0.05 on all linear layers. Raw counts are passed to the
model's internal normalization. Only adapter parameters are optimized.

For the 444-row hourly subset, the chronological split is:

| Period | Observations | Purpose |
| --- | --- | --- |
| First 300 hours | 300 | Training; 108 past/target windows |
| Next 72 hours | 72 | Select best epoch by validation MAE |
| Last 72 hours | 72 | Final test after epoch selection |

Default context is 192 hours (a multiple of TimesFM's 32-observation input
patch), with a one-hour horizon. Future targets cannot cross split boundaries.
Rolling validation/test contexts can include observations revealed earlier in
those periods; the adapter is not updated on them. This is repeated one-hour
forecasting, not a 72-hour forecast issued once. Irregular intervals are rejected.

The output directory must be new. Ignored `model-runs/` stores `adapter/`,
`report.json` and `test_predictions.csv`. Reports record data hash, split
boundaries, settings, package versions, validation scores per epoch and test
MAE/RMSE for persistence, moving average, zero-shot TimesFM and the selected
LoRA adapter. The report also says whether validation beats the base model;
training does not imply improvement. No adapter is automatically activated in
the autoscaler. The test period is held out from our fine-tuning, but it was
part of previously inspected data and pretraining overlap is unknown. Results
are exploratory and need confirmation on subsequently collected data.

First local run on October 20, 2026:
`model-runs/timesfm-730-lora-2026-10-20/`. Three CPU epochs updated 1,382,912
adapter parameters; epoch 3 had the best validation MAE. On the same 72 held-out
one-hour targets:

| Forecast | MAE (players) | RMSE (players) |
| --- | ---: | ---: |
| Persistence | 68,641 | 86,375 |
| Moving average (192 hours) | 260,793 | 292,093 |
| TimesFM zero-shot (Transformers) | 15,819 | 19,904 |
| TimesFM LoRA | 16,050 | 19,835 |

LoRA improved validation MAE slightly, but its test MAE was about 1.46% worse
than the base model; test RMSE was about 0.35% lower. This first small experiment
does not establish an improvement from fine-tuning. The trained adapter is kept
for reproducibility; it is not selected as a replacement for the base model.

To load a saved adapter for forecasting with the same context configuration:

```python
import torch
from peft import PeftModel
from transformers import TimesFm2_5ModelForPrediction

base = TimesFm2_5ModelForPrediction.from_pretrained(
    "google/timesfm-2.5-200m-transformers",
    cache_dir=".model-cache", local_files_only=True, dtype=torch.float32,
)
model = PeftModel.from_pretrained(base, "model-runs/my-timesfm-run/adapter")
model.eval()
# past_counts must be the latest 192 hourly observations.
with torch.no_grad():
    prediction = model(
        past_values=torch.tensor([past_counts], dtype=torch.float32),
        forecast_context_len=192,
    ).mean_predictions[0, 0].item()
```

### Simulate predictive scaling

`scripts/simulate_scaling.py` uses the original pretrained TimesFM 2.5 checkpoint
to compare capacity decisions with a reactive rule and a fixed fleet. The LoRA
adapter is not used. This is an offline replay with no infrastructure actions.

```powershell
.\.venv-ml\Scripts\python.exe scripts/simulate_scaling.py --history-path data/external/steamcharts/730-hourly-after-zero.csv --appid 730 --output-dir model-runs/my-scaling-simulation
```

The model must already be cached; `--allow-model-download` explicitly enables
downloads. Use a new output directory for each run. Demo defaults are:

| Assumption | Value | CLI option |
| --- | --- | --- |
| Customer's share of global Steam players | 0.1% | `--player-share 0.001` |
| Capacity per instance | 100 players | `--players-per-instance 100` |
| Spare capacity | 10% | `--headroom-fraction 0.10` |
| Minimum fleet | 1 instance | `--minimum-instances 1` |
| Fixed comparison fleet | 16 instances | `--fixed-instances 16` |
| Price including startup | 0.10 EUR/instance-hour | `--vm-cost-per-hour 0.10` |
| Historical context | 192 hours | `--window-size 192` |
| Evaluated period | Final 72 hours | `--evaluation-hours 72` |

These are adjustable demonstration assumptions, not measured server capacities,
customer traffic shares or provider prices. The capacity rule is:

```text
customer_players = global_players * player_share
desired_instances = max(minimum, ceil(customer_players * (1 + headroom) / players_per_instance))
```

`reactive_capacity` applies this rule to the current observation;
`timesfm_capacity` applies the identical rule to the predicted next-hour count.
`fixed_capacity` requests the configured fixed fleet. Existing `threshold` and
`aggressive` behavior is unchanged; they are not the reactive baseline here,
because their prototype thresholds do not implement these capacity assumptions.
The new capacity strategies are available to the offline simulator and are not
registered as live CLI strategies.

The replay accepts consecutive hourly observations only. At hour t, it observes
actual demand, scores the already-ready fleet, and requests the fleet for t+1.
All starts and removals reconcile at the next hourly boundary, modeling one-hour
startup and delayed removal. Starting instances are billed while booting, and
departing instances are billed until removal. Pending instances provide no
player capacity. Demand is held at the sampled value for each modeled hour;
subhour changes are unknown. The last hour holds its existing fleet without
ordering resources for an unscored future period. Context warmup and resource
decisions before the evaluation period are replayed but excluded from totals.
Initial fleet size uses only the first observation and the shared capacity rule.

Forecast inputs include the current count and earlier observations only. Warmup
or a failed/invalid forecast falls back to the reactive rule; fallback decisions
are counted. Each call to the predictive strategy represents one new hourly
sample; it must not be fed repeated 30-second scrapes of an hourly series.

`report.json` records assumptions, source hash, evaluation period and costs,
instance-hours, scaling events, hours below capacity, and unserved player-hours.
`decisions.csv` records actual/modelled demand, predictions, ready/starting/
requested fleets, shortfalls and costs for each evaluated hour and strategy.
Unserved player-hours are modeled demand minus ready capacity, integrated over
the sampled hours; this is not observed rejected players or an SLA measurement.
Cost savings versus the fixed fleet alone do not establish a better policy:
compare shortfalls and scaling activity as well. No live cost metrics or their
meaning are changed by the simulator's cumulative experiment costs.

The first replay, `model-runs/scaling-simulation-730-2026-10-20/`, used these
defaults on the final 72 hourly observations:

| Policy | Simulated cost (EUR) | Hours below modeled capacity | Scaling events |
| --- | ---: | ---: | ---: |
| Reactive capacity | 72.20 | 4 | 43 |
| Original TimesFM capacity | 73.00 | 0 | 48 |
| Fixed 16 instances | 115.20 | 0 | 0 |

TimesFM incurred EUR 0.80 more than the reactive rule, with no modeled capacity
shortfalls versus four sampled hours for the reactive rule. It cost EUR 42.20
less than the selected fixed fleet, with more scaling activity. There were no
forecast fallbacks. This demonstrates the simulation's tradeoff under the stated
assumptions, not validated production savings or capacity guarantees.

### Run shadow mode

`scripts/shadow_autoscaler.py` is a separate read-only observer. It reads player
samples and live decision metrics from Prometheus and writes proposals without
calling an engine or resource controller. It cannot change the running fleet,
and forecast latency or failure does not block `main.py`.

Run it on the host in the existing ML environment:

```powershell
.\.venv-ml\Scripts\python.exe scripts/shadow_autoscaler.py --seed-history-path data/external/steamcharts/730-hourly-shadow-seed.csv
```

Omit `--seed-history-path` to collect new hourly history from scratch. The local
seed above contains 445 observations: the previously preserved 444-row clean
history plus an observed October 20, 07:00 UTC value from a refreshed source
snapshot. Source details/hashes are retained in sibling metadata; the earlier
training and simulation datasets were not overwritten. The model uses only
192 consecutive past/current observations, ignores future seed rows, and warms
up if a gap separates the seed from live data. Seed data are an input context,
not additional training.

Use `--once` for a single poll. Defaults: appid 730, local Prometheus port 9090,
30-second polling, cached original TimesFM weights, and the same demo customer
share/capacity/headroom as the simulation. CLI options include `--appid`,
`--prometheus-url`, `--metric-name`, `--window-size`, `--player-share`,
`--players-per-instance`, `--headroom-fraction`, `--minimum-instances`,
`--output-dir`, and `--allow-model-download`. No ML installation or changes to
Compose are needed if the host ML environment is already installed.

The observer accepts **one fresh source sample per UTC hour**, during the first
five minutes. A Prometheus range-vector query preserves the actual scrape
timestamp, and samples older than 90 seconds are rejected. A start after the
five-minute window waits until the next boundary. Frequent polls do not become
extra hourly observations. Missing hours are not filled: the model waits for a
new consecutive context and records reactive proposals during warmup/failure.
Each successful forecast targets the next UTC hour, typically slightly less
than 60 minutes after issue time because sampling/inference takes time.

Ignored `data/shadow/730/` contains:

- `hourly_observations.csv`: accepted live hourly samples, separate from seeds.
- `events.jsonl`: sessions, decisions, forecast outcomes, missed targets and
  status transitions. Decisions record source/aligned/issue/target timestamps,
  policy, reactive capacity proposal, TimesFM proposal and live rule snapshot.
- `latest_decision.json`: last accepted proposal, retained between polls.
- `status.json`: latest poll status; `process.json`: observer PID/process state.
- `metadata.json`: run configuration; `observer.lock`: single-writer lock.

When a fresh sample arrives at a forecast's target hour, the observer records
absolute/squared forecast error and the hypothetical capacity shortfall of its
proposal. A missed hour is logged as unavailable and is never scored against a
later measurement. Query failures do not create zero-demand samples. Missing
live decision metrics do not prevent forecasting. Legacy unlabeled live metrics
are marked `appid_verified=false`, since their game identity cannot be proved
from the metrics alone. Check the recorded decision timestamp/freshness; live
rule counts and customer-share capacity proposals use different policies.

Restarts recover accepted samples and the pending forecast without issuing a
second proposal for the same hour. A different policy/source/window requires a
new output directory. Only one observer can own each directory; a process exit
releases the OS lock. Stop a foreground observer with Ctrl+C. This is a host
process, with no automatic startup after a reboot. For a background observer,
inspect `process.json` and verify the PID belongs to `shadow_autoscaler.py`
before stopping that process; the file may be stale after an abrupt termination.

On October 20, 2026, shadow mode was started as a hidden background host process.
Its first accepted source sample near 08:00 UTC was 535,036 global players.
Original TimesFM predicted 573,551.5 for 09:00 UTC, suggesting 7 demo instances;
the reactive capacity rule suggested 6, while the live `threshold` snapshot
reported 4 running/desired instances. No resource action was issued by the
observer. The next-hour outcome had not yet arrived at verification time.
Recheck process and status files each session rather than assuming it is running.

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
controller (fake by default). The game configuration overrides the global
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

### Decision metrics

The autoscaler exports these gauges on `/metrics` (port 8001 by default):

| Metric | Meaning |
| --- | --- |
| `player_count` | Player count actually used in the last successful evaluation |
| `running_instances` | Current instance count reported by the selected controller |
| `desired_instances` | Strategy's requested count in the last successful evaluation |
| `scaling_action` | Last successful action: `-1` down, `0` unchanged, `1` up |
| `strategy{name="aggressive"}` | Selected strategy; info-style gauge with value `1` |
| `last_decision_timestamp_seconds` | Unix timestamp of the last successful evaluation |
| `scale_up_events_total` | Counter of successful scale-up operations since process start |
| `scale_down_events_total` | Counter of successful scale-down operations since process start |

Per-game mode adds `appid` to every metric, for example
`strategy{appid="730",name="aggressive"} 1`. Legacy single-game mode leaves
`appid` off the metrics. Injected unregistered strategies use their class name.
Strategy information is available at startup, but player count, desired count,
action, and decision timestamp are emitted only after a successful evaluation.
The entire decision snapshot is updated together after applying scaling.

Failed queries preserve the previous decision metrics and timestamp; they do
not report zero players or a new action. Query
`time() - last_decision_timestamp_seconds` to see how old a decision is.
`scaling_action` describes the latest successful evaluation, not a cumulative
event count; a scrape can miss intermediate actions between evaluations.
The default 30-second loop may report `0` after an earlier scaling operation.

The provisioned dashboard compares source player counts with the counts used
by the autoscaler, plots running and desired instances together, and shows the
latest action, selected strategy, and age of each decision. Rebuild the local
autoscaler with `docker compose up -d --build autoscaler` after code changes.
Grafana reloads the provisioned dashboard file automatically.

`promtool check metrics` reports a naming lint warning for `player_count`
because `_count` is normally reserved for histogram/summary counts. This name
is retained for the decision input gauge; Prometheus scrapes and queries it.

### Scaling dashboard

Grafana provisions three dashboards in the `Ruby Acorn` folder:

- `Ruby Acorn Autoscaler`: operational overview.
- `Ruby Acorn Customer Costs`: customer capacity and hourly cost comparison.
- `Ruby Acorn Scaling`: player count, desired and running instances, scaling
  events, selected strategy, and decision age. Open
  http://127.0.0.1:3000/d/ruby-acorn-scaling/ruby-acorn-scaling.

The scaling dashboard is defined in
`grafana/provisioning/dashboards/ruby-acorn-scaling-dashboard.json`. Use the
`Game` selector to compare configured appids; `All` also includes the legacy
unlabeled single-game series. Links connect the two dashboards and preserve
the selected time range.

Event counters count operations, not VMs: scaling from 0 to 4 is one scale-up
event. Unchanged decisions and failed queries do not increment them. They reset
when the autoscaler restarts. The event chart uses
`increase(scale_up_events_total[$__rate_interval])` and the corresponding
scale-down counter; Prometheus handles observed counter resets. These are
estimated increases over rolling windows, not exact event timestamps. An event
before the first scrape may not appear as an increase, so the dashboard also
shows each raw counter since restart. Overlapping windows can display the same
operation at more than one point; do not sum chart points as an event total.

### Simple cost model

The illustrative default price is **0.10 EUR per VM-hour**, compared with a
fixed baseline of **4 VMs per game**. The model uses the current running count,
not the strategy's desired count:

```text
dynamic_cost        = running_instances * vm_cost_per_hour
fixed_baseline_cost = fixed_baseline_instances * vm_cost_per_hour
savings             = fixed_baseline_cost - dynamic_cost
```

All three exported gauges are **EUR per hour at the current allocation**, not
accumulated spending. For 2 running VMs, they are 0.20, 0.40, and 0.20 EUR/hour.
For 5 VMs, savings is -0.10 EUR/hour; extra buffer capacity can cost more than
the fixed baseline. Savings are not clamped to zero.

Configure the assumptions on the host:

```powershell
.\.venv\Scripts\python.exe main.py --once --label appid=730 --vm-cost-per-hour 0.10 --fixed-baseline-instances 4
```

Or set `AUTOSCALER_VM_COST_PER_HOUR` and `AUTOSCALER_FIXED_BASELINE_INSTANCES`
in Compose `.env`. Both accept zero; negative values and non-finite prices are
rejected. The same price and fixed VM count apply separately to every game.
`appid` labels distinguish game costs; `sum(dynamic_cost)`,
`sum(fixed_baseline_cost)`, and `sum(savings)` give portfolio hourly estimates.

The scaling dashboard shows dynamic cost, fixed baseline, and signed savings.
Query failures retain the running allocation and therefore its estimated price;
they do not imply free VMs. The estimates use the controller's instance count, not real
invoices. Storage, traffic, billing granularity, and real provider pricing are
outside this model, and no cumulative-cost accounting is implemented.

### Customer cost dashboard

Open http://127.0.0.1:3000/d/ruby-acorn-customer/ruby-acorn-customer-costs.
The dashboard is provisioned from
`grafana/provisioning/dashboards/ruby-acorn-customer-dashboard.json` and focuses
on the customer's capacity and cost rather than scaling events:

- Current VM count across the selected games.
- Estimated dynamic cost, fixed baseline cost, and signed savings in EUR/hour.
- Below-target indicator and missing VM count relative to each game's strategy.
- Cost versus baseline over time, savings per game, and current VMs per game.
- Selected strategies and the age of the oldest available decision.

The `Game` selector supports one or multiple appids; `All` includes legacy
unlabeled single-game mode. Top-level numbers sum the selected games. Cost
queries use the `autoscaler` scrape job to avoid mixing unrelated metrics.
Links connect all three dashboards; the scaling and customer views preserve
the game selection and time range.

`Below strategy target` checks each game's running count against its desired
count; one under-target game makes the indicator red even if another has spare
VMs. The fake controller applies scaling immediately, so it normally shows
`At or above target`. It does not measure actual player capacity or an SLA.
Missing desired-instance metrics produce no comparison, not a healthy zero;
query failures can leave an older decision visible, so check decision age.
Costs are hourly estimates, not accumulated costs or invoices; negative savings
remain visible when buffer capacity exceeds the fixed baseline.

## Real local container scaling demo

`--controller docker` selects `DockerController` behind the same
`running_instances`, `scale_up`, and `scale_down` interface. `fake` remains the
default; the normal Compose services keep using it. This backend starts and
stops **real local Linux containers, not full virtual machines or game servers**.
Confirm with the teacher whether containers satisfy the VM feasibility criterion.

Run this on the host with Docker Desktop in Linux-container mode, the Docker CLI
on PATH, and the project installed in the virtual environment:

```powershell
.\.venv\Scripts\python.exe scripts/demo_docker_scaling.py --strategy threshold --prometheus-url http://127.0.0.1:9090 --report demo-results/threshold.json
.\.venv\Scripts\python.exe scripts/demo_docker_scaling.py --strategy aggressive --report demo-results/aggressive.json
```

The reproducible scenario uses synthetic counts `50, 150, 700000, 50`:

| Strategy | Observed running containers |
| --- | --- |
| `threshold` | 0 -> 1 -> 2 -> 4 -> 1 |
| `aggressive` | 0 -> 2 -> 3 -> 5 -> 2 |

The optional Prometheus URL adds a final evaluation using the live `appid=730`
sample. Counts come from `docker ps`, and the JSON report records real container
IDs at each step. Finally, the script removes its remaining containers and
verifies zero remaining resources. The last cleanup to zero is demo cleanup,
not a scaling decision: both existing strategies have a nonzero minimum.
`demo-results/` is ignored by Git; reports are local generated evidence.

To use the normal autoscaler against live Prometheus:

```powershell
.\.venv\Scripts\python.exe main.py --once --label appid=730 --controller docker --docker-namespace my-demo
docker ps --filter label=ruby-acorn.managed=true --filter label=ruby-acorn.namespace=my-demo
```

Without `--once`, the host loop continues and exposes controller-backed metrics.
Use `--metrics-port 8002` if the Compose autoscaler already occupies 8001; the
default Prometheus target still scrapes Compose port 8001, so a host loop on
8002 requires its own scrape target. This does not switch the existing stack's
metrics from fake instances to Docker resources.

Normal loop exit leaves resources running; restarting with the same namespace
and game recovers the actual count from Docker. Stop and remove only those
demo resources when finished:

```powershell
.\.venv\Scripts\python.exe -c "from scaler.infrastructure.docker_controller import DockerController; DockerController('my-demo', '730').cleanup()"
```

The backend uses `alpine:3.22` running `sleep infinity`, fetched by Docker if
missing. Each container has a 32 MiB memory limit, 0.1 CPU limit, 32 PID limit,
no network or host mounts, a read-only root filesystem, and an unprivileged
user. Stopping it removes it automatically (`--rm`). Ownership is scoped by
managed, namespace, and game labels; other containers are not scaling targets.
Use one autoscaler process per namespace, and separate namespaces for independent
demonstrations. The bounded prototype is not a concurrent fleet manager.

`--docker-max-instances` / `AUTOSCALER_DOCKER_MAX_INSTANCES` sets a limit of 1-5
containers **across all games in the namespace**, including stopped allocations.
Exceeding it raises an error rather than changing the strategy's desired count.
`AUTOSCALER_CONTROLLER` and `AUTOSCALER_DOCKER_NAMESPACE` also configure the host
loop. The Compose Python image does not contain a Docker CLI or mounted Docker
socket; this demonstration deliberately runs on the host.

Controller failures are logged without recording a successful decision/event.
After a partial failure, the loop refreshes the actual running count when Docker
is reachable; if Docker is unavailable, the last known count remains visible.
`--once` exits with code 1 on controller failure. Unit tests use an injected fake
CLI runner and never start Docker; the demo commands are explicit live checks.
