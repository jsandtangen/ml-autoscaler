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

Pegasus approved real VM test completed 2026-10-20. User explicitly approved
create -> ACTIVE -> SHUTOFF -> ACTIVE, final ACTIVE, no continuous scaling.
Dedicated ruby-acorn-pegasus-vm-test-730-01 now has UUID
1517a4dc-ad07-45da-8fd2-c87307a9f8fb and fixed IP 192.168.44.33 on ACIT4410.
DecisionEngine + PegasusController performed all three actions with the same
UUID; independent final API verification at 16:05:46 Europe/Oslo confirmed ACTIVE
and no task. Actual quota use: 1/10 instances, 1/12 vCPU, 2048/24576 MiB RAM,
1/10 ports; flavor root disk 50 GiB, no attached volume or floating IP. SHUTOFF
retained the same allocation. Initially no other VMs existed in this project;
final inventory has only this VM. Network/SG/rules/keypair checks passed.
Two local one-off verifier assumptions failed (SDK flavor id normalized to name;
security rule array ordering), then were corrected through read-only checks.
No cloud action failed; no duplicate creation or deletion. Original verifier
reports are preserved under ignored data/pegasus/. Test processes exited and
no automatic scaling manager, monitoring rollout or autostart was started.
See docs/pegasus-one-vm-result.md. No SSH/game readiness or real-player capacity
was verified. Approval does not cover continuous deployment, extra VMs, deletion
or further unscheduled state changes. Preserve this ACTIVE VM and existing Moria
services unless explicitly instructed otherwise.

Pegasus one-VM preparation (2026-10-20): user explicitly authorized creating
dedicated ruby-acorn-key if no suitable keypair existed. Fresh API inventory
still had no VMs/keypairs. Generated local Ed25519 key under
%LOCALAPPDATA%/RubyAcorn/openstack/keys/ruby-acorn-key, registered public key only,
and verified the returned public key matches. NTFS allows only current user;
never print/copy private key or credential values. No VM/security/network
mutation has occurred. Existing default SG permits egress and ingress from its
own group only; no new port rules are needed for API start/stop testing.
Fresh Compute quota/usage is 10/0 instances, 12/0 cores, 24576/0 MiB RAM;
Neutron port quota/usage 10/0. data/pegasus/one-vm-test.json is now a validated
single-VM config: cloud openstack, pool vm-test, appid 730, hard maximum 1,
selected Ubuntu 24.04 / aem.1c2r.50g / ACIT4410 / ruby-acorn-key / default SG.
Existing --config preflight passed, and DecisionEngine dry-run planned exactly
one create while reporting actual 0 and no VM actions. See
docs/pegasus-one-vm-plan.md for concrete proposed create/stop/start sequence,
120s cooldowns and final ACTIVE state. VM operations await explicit user approval;
do not interpret the authorized keypair creation as approval to create the VM.
No automatic deletion, cleanup, public IP, SG changes or continuous manager.

Pegasus authenticated read-only verification (2026-10-20) supersedes the earlier
unverified-access notes below. User-provided downloaded clouds.yaml was copied
outside repo/OneDrive to %LOCALAPPDATA%/RubyAcorn/openstack/clouds.yaml; NTFS
allows only the current Windows user. Never print/read its credential fields
into tool output. SDK cloud name is openstack, region Pilestredet. User confirmed
project ACIT4410_H26_jesan5100, ID 234f15222c5e4396851d2713697893b1.
Authentication, Compute, Image and Network reads succeeded outside the Codex
sandbox. Direct sandbox HTTPS failed; no TLS verification was disabled.
Compute quota/usage: instances 10/0, vCPU 12/0, RAM 24576/0 MiB; no VMs exist.
11 flavors and 7 active images were listed; see docs/pegasus.md for full inventory.
Recommend aem.1c2r.50g + Ubuntu 24.04 + existing ACIT4410 network for a later
approved infrastructure demo, not proven 100-player capacity. Network has DHCP;
default security group exists. No keypairs were returned, so concrete --config
preflight awaits an appropriate keypair; --discover preflight passed without
selecting fabricated resources. No VM or other cloud resource was changed.
inspect_pegasus.py now supports --discover --cloud openstack --expected-project-id
for sanitized, strictly read-only inventory/quota reporting. Downloaded OpenRC
was never sourced; only non-secret configuration fields were extracted.
Interactive setup helper had a Set-Acl privilege failure on an already protected
directory; it now skips redundant ACL writes and is tested with synthetic input.
340 tests passed, including the protected-directory retry regression. Evidence is ignored
under data/pegasus/. No push/merge, deployment or continuous Pegasus manager.

Pegasus integration work (2026-10-20) now has a separate SDK backend at
src/scaler/infrastructure/pegasus_controller.py, selected with --controller
pegasus / --pegasus-config. Dry-run is the default; real changes require the
CLI-only --pegasus-apply AND explicit user approval. Existing Moria approvals
do not authorize Pegasus mutations. Maximum allocated pool is 6; project,
name and metadata must all match. Stopped VMs are reused before creation.
No delete operation exists. Compute quotas, operation waits, local cooldown,
single-host state-directory lock and persistent uncertain-create receipt guard
mutations. One manager across hosts remains required. See docs/pegasus.md and
pegasus.example.json; scripts/inspect_pegasus.py is strictly read-only.
Unauthenticated HTTP checks confirmed Horizon and Identity v3 reachable from
this workstation, but authenticated Compute/API roles/quota remain unverified.
No Pegasus credentials were read and no Pegasus resources were changed.
Existing monitoring configuration/deployment and Moria processes were untouched.
Optional .[openstack] uses openstacksdk>=4,<5. Test environment under ignored
data/pegasus-test-venv uses Python 3.12.11 / SDK 4.21.0; existing .venv could not
run pytest (missing unicodedata) and was not replaced. Infrastructure telemetry
has pegasus_ names; scrape examples prefix all names to preserve existing jobs.
Verification: 334 pytest tests passed, including SDK resource-contract checks,
with four SDK deprecation warnings; git diff --check passed. Authenticated
inventory, real one-VM/1-3 scaling and Prometheus scraping remain manual staged
checks pending local Pegasus configuration and explicit mutation approval.

Latest read-only audit (2026-10-20): user asked which tutorial VMs are still
needed and how to expand the pool; no cleanup/reuse was authorized or performed.
See docs/moria-vm-audit.md. All five known VM identities/statuses remain as before.
MORIAVM is the required management/Tailscale/OpenStack route; keep it. The
separate stopped moiravm is not that management VM, and its root disk remains
uninspected. internalvm runs a FastAPI exercise on port 10080; no Docker volumes
or mounts, Git working tree clean. No Ruby Acorn dependency was found, but other
course/external use is not ruled out. prometheus-vm still runs all five school
monitoring services with persistent volumes; preserve it. Other tutorial VM
names were absent from this authenticated project only; manager is a keypair.
Current configured VM totals: 5 instances, 6 vCPU, 8192 MiB; quotas 5/10/10240.
Two additional acit-small VMs would total 7/8/10240, so requesting instance quota
7 is the least disruptive proposed route to a three-VM pool. No quota request
was sent. Reuse of internalvm or moiravm needs explicit approval and preservation
planning. More VMs alone may still saturate under the current demo demand policy.

Latest priority (2026-10-20, supersedes the earlier plan B decision for scaling):
the user explicitly requested real VM control in Moria and confirmed that
start/stop of a fixed pool satisfies the course requirement. Defer further
TimesFM development; leave its existing observer separate. Preserve school
monitoring and all four existing VMs. Read-only Moria OpenStack access works via
ubuntu@moriavm and existing OpenRC application credentials; never print/copy them.
Compute quota is 5 instances/10 cores/10240 MiB RAM. The approved dedicated
pool VM now occupies the fifth instance slot; stopped VMs still count as existing
instances. Additional pool VMs need a quota increase or explicitly approved reuse.

OpenStackController in src/scaler/infrastructure/openstack_controller.py now
supports explicit UUID allowlists plus ruby_acorn_pool/ruby_acorn_game ownership
properties. It performs only show/start/stop, waits for ACTIVE/SHUTOFF with no
pending task, and rejects unhealthy/transitional/unowned states. ACTIVE does
not establish game-server readiness. main.py supports --controller openstack
and --openstack-pool / AUTOSCALER_OPENSTACK_POOL; one matching appid per process,
maximum bounded to the pool size, default max change 2. SSH runs on the host,
with strict host-key verification and existing remote authentication. Only one
manager may own a pool; there is no distributed lock. Compose stays fake.
scripts/demo_openstack_scaling.py is read-only unless --apply is supplied and
requires an initially stopped pool plus a fresh report path for its engine demo.
See docs/moria-vm-pool.md for the concrete one-VM creation/start/stop proposal.
The user approved that one-VM creation and start/stop test, executed below.
Verification: 311 pytest tests passed and diff whitespace checks passed.
A real read-only call through the new SSH transport returned SHUTOFF for an
existing VM; the ownership guard correctly rejected that unowned VM. The installed
OpenStack CLI does not accept --os-api-timeout; use the remote timeout utility.
On 2026-10-20, created ruby-acorn-pool-01, UUID
d41249d4-8b13-49d8-956c-0d95b12e4bc7, IP 192.168.1.229 on ACIT4410,
acit-small (1 vCPU, 1024 MiB RAM, 20 GiB disk), ubuntu-26.04-moria, manager
keypair, existing default security group, no floating IP. Both ownership
properties were verified. Console reported final cloud-init completion at
12:40:33 UTC. Initial stop through OpenStackController reached SHUTOFF.
The real DecisionEngine/ReactiveCapacityStrategy demo with synthetic counts
100000 then 0 passed SHUTOFF -> ACTIVE -> SHUTOFF at 14:42-14:44 Europe/Oslo.
It used minimum 0 for the demo (normal live minimum remains 1) and max change 2;
the single VM changed once per cycle. This verifies real VM operations, not game
readiness, real-player-triggered scaling, or a two-VM simultaneous change.
Report: ignored demo-results/moria-vm-start-stop-2026-10-20.json.
Pool config: ignored data/moria/pool.json. Creation, first boot, initial stop and
final verification evidence are under data/moria/. The final API check at
14:45 local confirmed the new VM SHUTOFF and all four original VM IDs/statuses
unchanged. No existing VM, school service, network or security rules were modified.
No continuous OpenStack loop was started and Compose still uses fake control.
Approval covers this dedicated creation/test, not deletion, reuse of old VMs,
quota changes or continuous deployment. TimesFM development remains deferred.

Subsequent user instruction "Fortsett til neste steg" authorized the described
next step: real-player-driven continuous VM control plus local Grafana telemetry.
This supersedes the earlier test-only running-state limitation. On 2026-10-20
at 14:51 Europe/Oslo, started a hidden host main.py process with controller
openstack, data/moria/pool.json, appid 730, reactive_capacity, local Prometheus,
minimum=maximum=1, max change 2, interval 30 and metrics 127.0.0.1:8002.
Worker PID was 5252, launcher 10392; data/moria/live-process.json records identity
and logs. Verify identity/fresh metrics; the receipt is not a heartbeat.
No auto-start after Windows reboot is configured. One manager must own the pool;
do not run demo_openstack_scaling.py concurrently with live management.
First live decision (1015537 global players) confirmed 0 -> 1 at 14:52,
then another confirmed 1 -> 1. The VM is now ACTIVE, not left stopped as after
the earlier demo. With min=max=1, normal demand variations cannot scale it down.
Raw modeled requirement was 12; the one-VM pool does not satisfy that demand.
No game-server readiness was established and assumptions remain demo capacity.

Prometheus has the additional job moria_vm_autoscaler targeting
host.docker.internal:8002, appid=730/pool=ruby-acorn. All its exported metric
names are prefixed moria_ via metric_relabel_configs. This preserves existing
fake dashboards and shadow source queries without mixing controllers.
Grafana provisions ruby-acorn-moria-vms-dashboard.json (17 panels), URL
http://127.0.0.1:3000/d/ruby-acorn-moria-vms. promtool config validation passed;
local Prometheus reloaded using SIGHUP. All four targets were UP, Grafana API
confirmed provisioning, all 19 panel queries returned data, and unprefixed
running_instances still belonged only to the fake autoscaler job. Visual browser
QA was unavailable (no connected browsers). Evidence is stored in ignored
data/moria/live-monitoring-verification.json. Live history is separate at
data/moria/live-player-history.csv; this run does not read TimesFM forecasts.
Existing school services/VMs/network rules were not changed. Only the dedicated
pool VM is managed. No deletion, old-VM reuse or quota change is authorized.

Ruby Acorn is an early Python autoscaler prototype. It reads game player counts
from Prometheus, calculates a desired number of VM instances, and applies a
scaling decision through a controller. The default is a fake in-memory controller;
an optional host Docker controller starts/stops bounded local containers, not
full virtual machines. Although the README names
the project `ml-autoscaler`, live resource control still uses rule strategies.
An original TimesFM capacity strategy and historical replay are available
offline; no predictive strategy is enabled in the live loop.

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
| `src/scaler/timesfm_forecasting.py` | Lazy pretrained TimesFM 2.5 adapter for offline evaluation |
| `src/scaler/training.py` | Chronological train/validation/test windows and forecast scoring |
| `scripts/finetune_timesfm.py` | Offline TimesFM Transformers LoRA training and held-out comparisons |
| `src/scaler/strategies/capacity.py` | Explicit customer share, capacity and reactive capacity rule |
| `src/scaler/strategies/predictive.py` | Past-window forecasts to capacity with reactive fallback |
| `src/scaler/simulation.py` | Hourly resource/cost/shortfall replay without infrastructure actions |
| `scripts/simulate_scaling.py` | Compare reactive, original TimesFM and fixed capacity offline |
| `src/scaler/data/shadow_source.py` | Read-only timestamped source and live-decision snapshots from Prometheus |
| `src/scaler/shadow.py` | Hour-aligned shadow proposals, continuity checks and next-hour outcome scoring |
| `scripts/shadow_autoscaler.py` | Independent host shadow observer, persistent journals and single-writer lock |
| `scripts/evaluate_forecasts.py` | Offline baseline comparison from player history, with JSON output |
| `scripts/simulate_scaling.py` | Hourly capacity replay; optional comparison of fleet limits reusing identical forecasts |
| `src/scaler/exporter/simulation_metrics.py` | Read-only export of a completed offline limit comparison, separate from live costs |
| `src/scaler/strategies/base.py` | Abstract `ScalingStrategy` contract |
| `src/scaler/strategies/threshold.py` | Current threshold-based instance calculation |
| `src/scaler/strategies/aggressive.py` | Threshold baseline plus one buffer instance |
| `src/scaler/engine/scaling_limits.py` | Shared min/max/rate-limit planning, no controller access |
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
  rows and duplicate timestamps. Optional pretrained TimesFM evaluation is
  available; offline LoRA training/scaling replay are separate and no live
  predictive scaling is enabled.
  Run `.\.venv\Scripts\python.exe scripts/evaluate_forecasts.py --appid 730
  --window-size 10 --horizon 1` against default `data/player_history.csv`, or
  supply `--history-path`. At least window size + horizon observations are needed
  per evaluated game. Later models must be compared on the same chronological
  evaluation period and future targets.
- `--include-timesfm` in `scripts/evaluate_forecasts.py` adds pretrained TimesFM
  2.5 (`google/timesfm-2.5-200m-pytorch`) via optional `.[ml]` dependencies
  (`timesfm[torch]==2.0.2`). It uses the same windows and targets as baselines,
  returning prediction step `horizon`, with no fitting or future inputs.
  Dependencies/weights load lazily once; graph compilation is disabled.
  Cache defaults to ignored `.model-cache/`; `--model-cache-dir` overrides it
  and `--local-files-only` prevents downloads. Invalid forecasts fail explicitly.
  Unit tests use a fake model without PyTorch/network. No live scaling uses ML.
- `scripts/finetune_timesfm.py` uses optional `.[training]` dependencies
  (Transformers 5.20.2, PEFT 0.21.2, PyTorch) and
  `google/timesfm-2.5-200m-transformers`, separately cached from native PyTorch
  inference weights. It trains rank-4 all-linear LoRA on raw counts with internal
  normalization. Defaults: context 192, horizon 1, validation/test 72 each,
  3 epochs, batch 4, lr 5e-5, seed 42, 4 CPU threads, float32; CPU if no CUDA.
  Context must be a multiple of 32. Data must have regular increasing intervals.
  Targets are disjoint by chronological split, with entire multi-step horizons
  contained in one split. Earlier revealed observations may enter rolling
  validation/test contexts without fitting on their targets. Best trained epoch
  is selected by validation MAE; test is scored only after selection, alongside
  adapter-disabled zero-shot and the same two baselines. Models are not deployed.
  Require a fresh `--output-dir`, preferably under ignored `model-runs/`.
  Save PEFT `adapter/`, `report.json` and `test_predictions.csv` (settings, data
  hash, versions, boundaries, epoch/held-out scores). Unit tests cover splits,
  leakage boundaries, gaps and failure handling without importing ML libraries.
- Strategies are selected by `--strategy` or `AUTOSCALER_STRATEGY`, defaulting
  to `reactive_capacity`. `src/scaler/strategies/registry.py` maps names to implementations;
  unknown names fail at startup. Add algorithms there without changing the loop.
- `reactive_capacity` is registered and is the active default. Predictive capacity remains shadow-only.
  `CapacityPolicy` uses customer share of global counts and desired fleet =
  max(minimum, ceil(customer demand * (1+headroom) / players per instance)).
  `ReactiveCapacityStrategy` uses current counts; `PredictiveCapacityStrategy`
  uses a past-only rolling window and a next-hour forecaster. Warmup and invalid/
  failed forecasts fall back to the same reactive rule and expose status.
  One call represents one new hourly sample; do not feed this strategy repeated
  30-second scrapes. Legacy threshold/aggressive strategies are unchanged.
- `scripts/simulate_scaling.py` replays hourly history with the original native
  TimesFM 2.5 checkpoint, never LoRA. Demo assumptions: share 0.001, 100 players
  per instance, 10% headroom, minimum 1, fixed fleet 7, maximum 7, max change 2, EUR 0.10 per instance-
  hour, context 192, final 72 hours scored. All are CLI-configurable except the
  one-hour forecast/startup/reconciliation period. Require consecutive hourly
  observations; decisions at t affect ready fleet at t+1. Startup instances are
  billed but provide no capacity; removals wait until the next boundary. Actual
  demand is held for each modeled hour. Final hour holds its existing fleet.
  Replay warmup/resources before the scored period, without charging those
  hours. Report model fallbacks/successes, cumulative simulated cost, instance-
  hours, scale events, capacity shortfall hours and unserved player-hours.
  Use a new output directory under ignored `model-runs/`; save report.json and
  decisions.csv. Model downloads are disabled unless --allow-model-download.
  This creates no infrastructure resources and changes no live cost metrics.
- Shadow mode runs separately via `scripts/shadow_autoscaler.py`, never inside
  `main.py`/controllers. Read original TimesFM weights from cache by default;
  downloads require --allow-model-download. Poll Prometheus every 30s; accept
  only the first fresh source sample per UTC hour within 300 seconds after the
  boundary (source age <=90s). Read scrape timestamps from a 5m range vector,
  not query evaluation timestamps. Proposals target the next UTC boundary.
  Need 192 consecutive hourly observations; no synthetic gap filling. Optional
  --seed-history-path primes past inputs without training. On gaps, warm up
  again; on model failure, log a reactive capacity proposal with fallback status.
  Preserve live rule control/metrics unchanged. Capture actual live rule metrics
  when available; capacity_source_info verifies legacy source identity when present.
  Unlabeled snapshots without that info have appid_verified=false.
  Log outcome only at the exact target hour; missed hours are not scored against
  later demand. Hypothetical shortfall uses demo capacity, not real SLA data.
  Persist hourly_observations.csv, events.jsonl, latest_decision.json, status.json,
  process.json and metadata.json under ignored data/shadow-capacity/APPID (older pre-limit logs remain in data/shadow/APPID). Recover samples
  and pending forecast on restart; avoid duplicate hourly proposals. One process
  owns each directory via OS lock; changed config requires another directory.
  No Compose changes/ML runtime installation are required for the existing host
  .venv-ml. A host observer does not restart automatically after reboot.
- Each evaluation computes the desired count once; `DecisionEngine` applies it
  via `apply_desired_instances(desired)`. `evaluate(player_count)` remains available.
- `aggressive` selects `AggressiveStrategy`: the threshold baseline plus one
  buffer instance (2 at zero players, 5 at 600 or above). It prioritizes spare
  capacity over cost, with no delay or hysteresis. `threshold` remains an explicit legacy option, not the default.
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
  `730: reactive_capacity` and `570: reactive_capacity`; no `cost_saving` strategy exists.
- `ThresholdStrategy` returns 1 below 100 players, 2 from 100 to 299, 3 from
  300 to 599, and 4 at 600 or above. These are prototype values, not validated
  production capacity limits.
- `DecisionEngine.evaluate(player_count)` reads the current count, scales up or
  down toward a bounded target, at most 2 instances per cycle by default,
  and returns the actual resulting count. Equal counts
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

## Current capacity behavior (2026-10-20)

- Reuse `CapacityPolicy` for global-to-customer conversion: share 0.001,
  100 players/VM, headroom 0.10. `required_instances()` preserves the raw ceil
  before minimum/maximum; desired minimum is 1. 535000 -> 6; 700000 -> 8.
- `ScalingLimits` bounds desired fleet, then limits each change to 2 per cycle.
  Fake maximum defaults to 7; Docker uses the lower of this and its existing
  configured namespace limit (default 5). No new controller/provider is added.
  Rediscovered fleets above a new maximum shrink gradually, respecting the rate
  limit. No cooldown/hysteresis exists; do not claim one was added.
- Main CLI/env: player-share, players-per-instance, headroom-fraction,
  minimum-instances, max-instances, max-scaling-change, max-sample-age-seconds;
  environment names are AUTOSCALER_PLAYER_SHARE, AUTOSCALER_PLAYERS_PER_INSTANCE,
  AUTOSCALER_HEADROOM_FRACTION, AUTOSCALER_MINIMUM_INSTANCES,
  AUTOSCALER_MAX_INSTANCES, AUTOSCALER_MAX_SCALING_CHANGE,
  AUTOSCALER_MAX_SAMPLE_AGE_SECONDS. Per-game strategy selection is preserved.
  Threshold/aggressive classes remain legacy opt-ins for older demonstrations.
- Main sets GameDataClient's freshness selector to last_over_time(metric[90s]);
  standalone clients default to their old instant-query behavior unless given
  max_sample_age_seconds. Failed/stale/malformed input skips scaling and records
  input_available=0; zero is valid demand and targets the configured minimum.
- Capacity demand metrics update on valid input even if control later fails:
  global_player_count, customer_player_count, required_instances (uncapped),
  cycle_target_instances, maximum_instances, max_scaling_change and
  capacity_demand_timestamp_seconds. Derived unmet_instances and
  unmet_player_capacity use actual running counts; capacity_limit_shortfall_instances
  exposes demand beyond the maximum. Successful-decision/event metrics retain
  success-only semantics. The scaling dashboard now shows the separate values.
- Shadow mode shares both policy and limits. New default output directory is
  data/shadow-capacity/APPID; existing pre-limit configurations require a new
  directory. Logs separate raw shadow_required_instances, bounded target and
  shadow_cycle_target_instances (only with fresh game-verified live counts).
  Live source info and capacity settings are read from Prometheus. A fresh
  configuration mismatch suppresses proposals. No predictive strategy is
  registered for live control. TimesFM and VM operations remain separate.
- Main optionally reads latest_decision.json under --shadow-decisions-dir /
  AUTOSCALER_SHADOW_DECISIONS_DIR, default data/shadow-capacity (Compose:
  /app/data/shadow-capacity). Only unexpired matching-game/model/policy forecasts
  expose predicted_* gauges; their requirements are recomputed by CapacityPolicy.
  These files never influence controller decisions. Predictions expire at their
  target timestamp and are omitted rather than emitted as zero.
- The simulation also applies the same fleet and rate limits; fixed baseline
  now defaults to 7. Archived earlier 16-instance reports predate these limits.
  Docker demo repeats high/low samples and verifies each limited cycle target.

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

On 2026-10-20, the existing `.venv` Python failed to start (exit -1073741515).
A separate `.venv-ml` was created using installed Python 3.12.11, preserving the
old environment. Use `.\.venv-ml\Scripts\python.exe` for this workspace until
the original environment is repaired. Install `.[dev,ml]` for TimesFM work.
No `data/player_history.csv` was present during inspection. A later external
history acquisition is documented below; it does not populate the live file.
The full suite passed with 166 tests. TimesFM/PyTorch were installed in
`.venv-ml`; a real checkpoint inference and the full three-model CLI comparison
were verified on 66 synthetic observations (window 64, horizon 2). The CLI
verification used cached weights with `--local-files-only`. Generated synthetic
input/report are under ignored `demo-results/timesfm-synthetic-*`; these are
integration evidence, not training data or evidence of real-game accuracy.

On 2026-10-20, real SteamCharts appid 730 history was downloaded locally under
ignored `data/external/steamcharts/`. The raw JSON contains mixed resolutions;
`730-hourly.csv` retains only the consecutive hourly suffix (720 observations,
2026-09-20 07:00 to 2026-10-20 06:00 UTC). Timestamps are rounded to the nearest
UTC hour (max shift 207 seconds), without interpolating counts or filling rows;
the raw JSON preserves original timestamps. There are no missing/duplicate hour
slots. A suspicious zero at 2026-10-01 18:00 UTC remains in the full CSV.
`730-hourly-after-zero.csv` starts after it and has 444 consecutive observations.
Use this initial subset with window 168, horizon 1 (hours, not minutes); it gives
276 evaluation targets. Metadata includes source URLs, hashes and quality flags.
Baseline evaluation and one actual TimesFM inference succeeded on the subset;
full TimesFM rolling scores are not yet computed. Treat results as exploratory,
and verify on newly collected data later. No explicit redistribution license
was verified; keep source data local and retain attribution.
The live Docker stack was running when checked. A separate raw Prometheus
snapshot under `data/external/prometheus/730-last-7-days.json` has 438 actual
scrape samples and a roughly 3.87-day gap; do not treat it as contiguous or merge
its cadence with hourly external data. Recheck live status each session.

On 2026-10-20, the first actual TimesFM LoRA run completed under
`model-runs/timesfm-730-lora-2026-10-20/` on CPU. Data split: 300/72/72 hours;
192-hour context, one-hour horizon, 108 training windows, 3 epochs, rank 4,
1,382,912 trainable adapter parameters. Best validation epoch was 3. Test MAE /
RMSE (72 identical targets): persistence 68640.72 / 86375.38; moving average
260793.30 / 292092.67; Transformers zero-shot 15819.30 / 19903.99; LoRA
16049.56 / 19835.22. Validation MAE improved slightly, but test MAE worsened
about 1.46% (RMSE improved about 0.35%); do not claim fine-tuning beat the base
model overall or deploy it automatically. Adapter weights were updated and
saved separately; reports/predictions preserve the experiment. Full suite:
186 tests passed; installed dependency compatibility and diff checks passed.

On 2026-10-20, the user chose the original pretrained TimesFM model and approved
an offline customer-share simulation with configurable demo capacity and an
hourly horizon. First run: `model-runs/scaling-simulation-730-2026-10-20/`, last
72 hours of `730-hourly-after-zero.csv`, original native PyTorch checkpoint,
192-hour context. Defaults as documented above. Results: reactive cost EUR
72.20, 4 modeled under-capacity hours, 43 scale events; TimesFM EUR 73.00,
0 under-capacity hours, 48 scale events; fixed 16 instances EUR 115.20,
0 under-capacity hours. Zero model fallbacks. All 216 scored decision rows,
cost/shortfall totals and next-hour fleet reconciliation were checked. Full
suite: 216 tests passed. These are exploratory simulation results under demo
assumptions, not production savings/SLA evidence. Predictive infrastructure
control remains future integration; shadow logging is implemented below.

On 2026-10-20, separate read-only shadow mode was implemented and tested with
32 new unit tests (248 total passed). Refreshed SteamCharts raw snapshot
730-chart-data-shadow-start.json included a new 07:00 UTC observation. A separate
445-row 730-hourly-shadow-seed.csv and source/hash metadata preserve the previous
training/simulation files. First live shadow sample near 08:00 UTC: 535036
players, forecast 573551.5 for 09:00 UTC, proposal 7 under demo capacity (reactive
capacity 6); live threshold snapshot running/desired 4. The next-hour outcome
was not yet available. A hidden background host observer was started; consult
data/shadow/730/process.json and verify the OS process/status on each session.
The observer changes no resources and does not block the existing autoscaler.

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
5. Keep real VM control, live ML strategies, cooldown/hysteresis, and production
   deployment as future design work unless explicitly requested.

Real VM control, live ML strategies, cooldown/hysteresis, and production deployment
remain future design work unless explicitly requested.


On 2026-10-20, shared capacity became the active default (`reactive_capacity`).
Controller interfaces were preserved. The existing Compose autoscaler was
rebuilt/recreated with fake control only; no managed Docker game containers or
cloud VMs were started. Live Prometheus verified 578922 global players, 578.922
simulated customer players, 7 required/desired/running instances, no unmet demand,
and unexpired predicted_required_instances=7. Source identity, active policy and
limits (1/7/2) were verified through ShadowPrometheusClient. Existing shadow logs
were preserved in data/shadow/730; accepted live samples and the still-unexpired
original forecast were migrated to data/shadow-capacity/730, with a migration
event/provenance hash and no new forecast issued for that same hour. The old
observer was stopped after OS identity verification; the new hidden observer
runs with shared limits. Recheck process/status each session.

The bounded replay under model-runs/scaling-capacity-limited-730-2026-10-20
used maximum 7 and change 2 per hourly cycle. All three policies had 43/72 hours
below modeled nominal capacity; costs EUR 48.30 reactive, 48.70 original TimesFM,
50.40 fixed 7. Do not claim forecasting removes a deficit imposed by the cap.
Current active source freshness defaults to 90 seconds; tests cover zero,
missing/stale/invalid data, partial controller failures, budget/rate-limit unmet
demand, shadow policy mismatch and forecast expiry. Predictive resource control
is still unavailable; enabling it needs explicit configuration and further
shadow evaluation and provider/startup/capacity validation.

Final verification for the shared-capacity change: 276 pytest tests passed;
Grafana dashboard JSON (27 unique panel IDs), diff whitespace and Compose
configuration were checked. Live checks use fake control only; Docker-controller
unit tests use the injected fake CLI runner. No real VM/container scaling demo
was executed for this change.

On 2026-10-20, the user requested independent real VMs, maximum two changes per
cycle, comparison of fleet limits 7/10/15, and retained TimesFM shadow mode.
Local discovery found Windows 11 Home, Docker Desktop with WSL2, and no usable
Hyper-V module/Get-VM, VM management service, or VirtualBox/VMware/QEMU CLI.
The user explicitly declined substituting containers for independent VMs and
will provide the VM host. Ask for hypervisor/host, management connection and
owned VM IDs or template before implementing provider-specific control. Do not
claim Docker containers satisfy this request. Active Compose remains fake;
no new VM backend or paid cloud resources have been enabled.

The existing reactive Docker demo was separately verified with real container
IDs: 0 -> 1 -> 1 -> 3 -> 5 -> 3 -> 1, at most two identity changes per cycle,
then cleanup to zero. Evidence is in ignored demo-results/
reactive-capacity-containers-verified-2026-10-20.json. Cleanup is outside normal
scaling decisions. It proves container operations only, not independent VM
startup or game-server readiness.

scripts/simulate_scaling.py --compare-max-instances 7 10 15 reuses cached original
TimesFM predictions across identical past-only windows, keeps the evaluation
period/policy/rate limit identical, and uses fixed allocation equal to each cap.
Outputs are per-cap report.json/decisions.csv and completed comparison.json.
The 72-hour replay under model-runs/scaling-limits-730-2026-10-20 produced:
cap 7: reactive EUR48.30/43 deficit hours, TimesFM48.70/43, fixed50.40/43;
cap 10: reactive61.60/28, TimesFM61.80/27, fixed72.00/27;
cap 15: reactive72.20/4, TimesFM72.80/0, fixed108.00/0.
Reactive unserved player-hours: 14662.423 / 4370.798 / 102.282; TimesFM:
14662.423 / 4315.516 / 0. These are demo-model results, not verified capacity.

Compose now configures AUTOSCALER_SIMULATION_REPORT_PATH to
/app/data/scaling-comparison.json, copied from the completed comparison.
Host main.py supports --simulation-report-path / the same environment variable.
The existing exporter reads the optional JSON on scrape, validates comparable
data/period/model/policy and nonnegative finite totals, and exposes separate
simulation cost/deficit/duration gauges with appid/strategy/max_instances labels.
Invalid reports show simulation_report_available=0 and no fabricated results;
creation timestamp remains visible for valid reports. These are offline totals,
not live EUR/hour. controller_info identifies backend/resource type explicitly.
Ruby Acorn Scaling has 35 unique panels including offline comparisons, report
status/time/duration and actual controller type. Live Prometheus verified all
nine comparison series and an unexpired shadow predicted_required_instances.
No predictive strategy controls live resources. Retain the existing observer
and logs; no observer configuration change was needed for this milestone.

The user then clarified that no VM host or VMs exist. They selected the local
Windows PC, preferably VirtualBox/VBoxManage, with 2–3 small Linux VMs cloned
once from a template and then started/stopped as a fixed pool. They explicitly
requested installation/setup guidance BEFORE controller implementation.
Do not implement or enable VirtualBox control yet. docs/local-virtualbox-setup.md
records resources, official compatibility sources and staged setup. Windows 11
Home cannot host the normal Hyper-V role. WSL2 uses the Windows hypervisor;
Oracle documents VirtualBox coexistence through Windows Hypervisor Platform,
with potentially substantial performance degradation. Preserve WSL2/security
settings and test one 1 GiB/1-vCPU Debian guest first. Only about 3.2 GiB host
RAM was free; do not assume three guests fit. No hypervisor installation,
feature change or independent VM creation has been performed.
This historical VirtualBox plan was superseded by the later OpenStack discovery
and the user's plan B decision below. Do not treat VirtualBox installation as
the current next action. Full-VM start/stop verification remains outstanding.

Verification of comparison/telemetry: 288 pytest tests passed; diff whitespace,
Compose configuration and Prometheus configuration passed. Live Prometheus
exposed nine comparison series plus original TimesFM shadow forecast. Grafana
loaded the comparison panels. Fake control remains active.

On 2026-10-20, the user selected PLAN B after a read-only infrastructure/data
audit: keep ACIT4410's historical monitoring separate; continue using Ruby
Acorn's already-running local Compose stack. Do not install VirtualBox, create
VMs, deploy to the school monitoring VM or switch the active fake controller
merely because plan B was selected. TimesFM remains shadow-only. The user
requires approval before any infrastructure cleanup or modification, and before
backup execution. The user subsequently approved the proposed backup procedure,
the local destination below and password-protected AES-256 7z encryption,
including official 7-Zip software on Windows if necessary. This authorizes
backups and cleanup of artifacts created by that backup only, not deletion of
VMs, old data or service changes. The user enters the password locally; never
ask for it in chat, log it or put it in a command-line argument.

Existing SSH routes verified: local acit4410_key to ubuntu@moriavm; from there,
ubuntu@192.168.1.212 is internalvm, and ubuntu@192.168.1.179 requires the existing
manager_key with IdentitiesOnly=yes. No key contents were read or recorded.
MORIAVM 192.168.1.180 is the Tailscale/SSH management entry. internalvm hosts a
FastAPI exercise without Docker volumes or local Git changes. moiravm
192.168.1.196 is reportedly stopped; its disk contents remain unexamined. None
is used by the active local Ruby Acorn runtime. Do not delete any of them.

prometheus-vm runs one-node Swarm stack prom: Prometheus 2.36.2, Grafana 13.2.2,
Alertmanager, node-exporter and cAdvisor. It has 2 vCPU, 3.8 GiB RAM (about
2.8 GiB available at audit), a 19 GiB root filesystem (about 12 GiB free),
Python 3.14.4/venv, and no Compose plugin. prom_prometheus_data is about 664 MiB,
prom_grafana_data about 337 MiB. /home/ubuntu/prometheus contains modified
prometheus/prometheus.yml and grafana/config.monitoring plus a config backup.
Treat configuration backups as sensitive; never print credential values.
Prometheus admin API is already enabled, so a future approved TSDB snapshot
including head data needs no restart. For Grafana, propose SQLite Online Backup
plus other persistent files, not a raw copy of the live database. Persisted
Alertmanager silences/nflog files were both zero bytes. No backups were executed
during that initial read-only audit; the later approved backup is tracked below.

Approved backup destination: C:/Users/jsand/RubyAcornBackups/prometheus-vm/2026-10-20,
outside OneDrive/Git. scripts/backup_prometheus_vm.py installs a SHA-256-verified
official portable 7-Zip console under the user's LocalAppData (the normal
installer required Windows administrator elevation), creates a unique private
local staging folder, and invokes scripts/prepare_prometheus_backup.py via SSH
as root. The remote preparer creates only a new /var/backups/ruby-acorn/run-ID
tree and a TSDB snapshot including head, makes an online SQLite copy with
integrity_check, preserves Grafana assets/config/Alertmanager state, records
image digests and verifies unchanged running Swarm task IDs. It hashes every
regular backup file. The archive is streamed over SSH to private Windows
staging and every transferred file hash is checked before password handoff.
scripts/finish_backup_encryption.ps1 opens in a visible local console: 7-Zip
prompts for the password, encrypts with header encryption, and tests the archive
with user-entered password. The helper writes *-status.json and an archive
checksum only after successful verification, then removes its own local work
folder using validated resolved paths. Inspect the status file; do not claim
encryption complete from preparation alone. After status encrypted_and_verified,
backup_prometheus_vm.py --cleanup-verified STATUS_PATH validates the encrypted
archive hash and removes only that run's remote staging and created snapshot.
Original TSDB blocks, original Grafana DB, configuration and services are not
deleted or restarted. A full restore rehearsal remains a separate later step.

The games exporter at 172.31.255.1:1080 explicitly replays source history:
/health reports replay_epoch/current_interval/max_interval and 362 games;
189 player series were active at audit. Metric is steam_player_count with appid
and game metadata labels; player_count was absent from retained history.
Effective retention is 15d; retained CS:GO/Dota 2 samples spanned about 15.7d,
with one-minute scrape intervals and no timestamp holes, but around 12% zeros.
Both had a common 42h50m zero period while up=1. Do not interpret these as valid
zero global demand. Values typically update every five minutes. Source endpoint
/games/730/history was inspected in memory only: 69,120 regular 300-second
records covering 240 days, original UTC dates 2017-12-14 through 2018-08-10,
1,115 zeros (1.61%), longest positive contiguous segment about 5.27 days.
This is historical CS:GO, not live 2026 CS2. Keep source and scrape timestamps
distinct; do not combine replay with the existing local live Steam history.
Potential 15/30-minute forecasts require 3/6 steps at five-minute resolution,
not the current hourly observer unchanged. Data export, cleaning and model
experiments remain proposed work. No TimesFM install/inference was triggered
by the audit. Public dataset overlap with model pretraining is unknown.

Approved backup backup-20261020T103244Z-966a24 completed on 2026-10-20. It included
a TSDB snapshot with head, online SQLite backup (integrity_check=ok), Grafana
assets, configuration with local changes and Alertmanager state. All five Swarm
task IDs were unchanged during preparation. 1,281 files totaling 1,017,115,146
bytes were verified after transfer. User entered the encryption password only
in the local console. Receipt status is encrypted_and_verified; encrypted archive
is 471,407,348 bytes under the approved destination, named
backup-20261020T103244Z-966a24.7z. Its SHA-256 and *-status.json are adjacent.
The helper verified the encrypted archive and removed the local plaintext work
directory. Inspect receipt remote_cleanup for cleanup confirmation. No passwords
or key contents were exposed. 295 pytest tests passed, including backup content,
path and cleanup guards; PowerShell parsing and diff whitespace checks passed.
A complete application restore rehearsal has not been performed. Backup approval
does not authorize deleting VMs or original monitoring data.

On 2026-10-20 at 14:22 Europe/Oslo, the user approved restarting the stopped
host shadow observer. It was restarted hidden with the existing configuration,
output directory and original seed (same SHA-256); existing journals were retained.
The interpreter PID recorded in process.json was 15372 (launcher PID 25144).
Initial Prometheus poll succeeded with waiting_for_boundary, next sample due
15:00 local time. The missed 14:00 sample breaks the consecutive hourly context;
normal forecasts require 192 consecutive observations again, without filling gaps.
New process logs use restart-20261020-*.stdout.log / *.stderr.log in the same
shadow directory. Verify process identity and fresh status each session.
Local Compose services were running, all three Prometheus targets were UP,
Grafana database health was ok, and reactive fake control reached 7 instances.
No infrastructure configuration, controller choice or school services changed.
