# TimesFM shadow on Pegasus

Implemented on `feature/pegasus-ml-scaling`, 2026-10-21. This is a separate
read-only process. The rule autoscaler, its strategies, service unit, state and
VM pool are unchanged. No merge to main/master is part of this work.

## Model and data

- Pretrained `google/timesfm-2.5-200m-pytorch`, TimesFM package 2.0.2,
  CPU PyTorch 2.14.1. Checkpoint revision
  `1d952420fba87f3c6dee4f240de0f1a0fbc790e3`; no training/fine-tuning.
- Appid 730 at the course source is **historical CS:GO**, not current CS2.
  `/games/730/history` contains 69,120 original UTC observations at 300 seconds.
- Context is 64 observations (5h20m), selected before scoring because the
  current 128-point context crossed an invalid zero. One inference returns six
  steps; steps 3 and 6 are the 15/30 minute forecasts.
- Each model receives only past values. The downloaded full history contains
  future replay rows, so the runner truncates it at a validated current replay
  cutoff before any evaluation or inference. Future records never enter model
  input. Original source time and wall-clock issue time remain separate.
- Reject malformed timestamps, duplicates, negative counts, noninteger counts
  and non-five-minute alignment. Audit gaps and zero runs. Reject any model
  context or scored origin-to-target interval crossing a gap or zero; do not
  interpolate, fill holes or treat source zero periods as legitimate demand.
- `/health` sits behind Varnish and can be cached for several minutes even with
  no-cache headers. The ML reader uses a unique health query. The source's
  interval counter also runs one bin behind its elapsed epoch clock. Validate
  source elapsed time separately from host clock skew. For live input, require
  a fresh Prometheus sample matching exactly one of the last three published
  source bins; ambiguous, stale, rewound or unmatched data suppress forecasts.

## Code and capacity

`TimesFMForecaster.predict_steps` reuses one loaded model for both horizons.
`FiveMinuteShadowObserver` lives beside the original hourly `ShadowObserver` in
`src/scaler/shadow.py`. `scripts/shadow_autoscaler.py --five-minute` selects the
new path; the existing hourly mode and its journals remain intact.

Baselines are the last value and the arithmetic mean of the same context.
Capacity translation reuses `PredictiveCapacityStrategy`, `CapacityPolicy` and
`ScalingLimits`: player share 0.0001, 100 players/VM, headroom 0.1, min 0, max 1,
max change 1. Require a fresh matching live policy/limits snapshot before live
proposals. Export raw required capacity, capped recommendation and next-cycle
target separately. The active aggressive strategy has its own reserve/buffer;
the ML recommendation uses the shared capacity policy, not that extra reserve.
Live desired/running counts are included for comparison. Max=1 per game means
most recommendations coincide; this cannot demonstrate larger-fleet savings.

Persist forecasts and their source targets, pending outcomes and cumulative
scores atomically in the separate state directory. On restart, restore pending
forecasts and avoid issuing a second forecast for the same source bin. Score
only the exact target and only once. Invalid/missed targets are unavailable,
not zero-error outcomes. Invalid contexts suppress proposals; they do not affect
the independent rule autoscaler.

## Offline result on Pegasus

The last 288 candidate origins before the replay cutoff form an exploratory
chronological rolling holdout. Contexts use only observations available at each
origin. Both horizons and all methods share the same 197 accepted origins;
91 candidates crossed invalid source zeros. There was no model fitting or
parameter selection on these scores. Historical overlap with model pretraining
is unknown. This is a short replay test, not a production accuracy guarantee.

| Model | 15m MAE | 15m RMSE | 30m MAE | 30m RMSE |
| --- | ---: | ---: | ---: | ---: |
| TimesFM | 2228.98 | 4221.23 | 4888.51 | 8798.22 |
| Last value | 9628.94 | 11472.20 | 19508.21 | 22902.20 |
| Moving average (64 values) | 97489.09 | 109325.96 | 106813.37 | 118914.40 |

Errors are global source players, not customer players. The long mean baseline
lags the daily cycle; it is not a tuned competing model. Scored history covers
only the already-published replay prefix: 21,022 rows, no missing intervals,
844 zero rows and a longest zero run of 514 intervals (42h50m).

Evaluation took 87.08 seconds wall time / 41.28 seconds process CPU under a
50%-of-one-core quota. Reported process peak RSS was 2,136,879,104 bytes
(2037.9 MiB). Before testing, the manager had 2 vCPU, 3915 MiB RAM, about
3326 MiB available, no swap and negligible CPU load. Model files occupy about
925 MB; CPU-only wheels avoid CUDA downloads. The existing autoscaler kept PID
56459 with no restarts during evaluation and deployment.

## Isolated runtime and monitoring

- New service: `ruby-acorn-ml-shadow.service`, user/group `ruby-acorn-ml` with
  no login; separate `/opt/ruby-acorn-ml-shadow` and venv.
- State: `/var/lib/ruby-acorn-ml-shadow`; model cache:
  `/var/cache/ruby-acorn-ml-shadow`. Never use the autoscaler's state directory.
- No OpenStack SDK in the ML venv. Five-minute mode refuses `OS_*` environment
  variables. ProtectHome and inaccessible production paths prevent credential
  and autoscaler-state reads. The service has no capabilities or sudo access.
- CPUQuota=50%, MemoryHigh=1800M, MemoryMax=2300M, one Torch/OMP/MKL thread,
  lower scheduling priority. Model weights are cached before startup and
  HF_HUB_OFFLINE=1 prevents runtime model downloads.
- systemd denies IP access except the data source, monitoring host and localhost.
  It does not permit OpenStack API destinations. No VM, SG or public port change.
- Metrics listen on management fixed IP `192.168.45.220:8003`. Only the new
  `ruby_acorn_ml_*` family is exported. This endpoint is a second target of the
  **existing** `ruby_acorn_pegasus` job; existing metrics remain on port 8001.
- Error series use `evaluation="offline_replay"` or `"live_shadow"` and
  `model`/`horizon_minutes` labels. Live errors appear only after targets arrive.
  Invalid/stale/expired forecasts are omitted. Observer freshness and forecast
  availability are explicit. Forecast graph points are plotted at issue time,
  whereas scoring uses original source target time.
- `target_wall_timestamp` is a nominal validity/expiry time (issue time plus
  horizon). Replay cache delays can make the corresponding source target arrive
  later. It is not a guarantee of wall-clock replay delivery; exact evaluation
  always uses `target_timestamp` on the original five-minute source clock.
- Additive dashboard UID: `ruby-acorn-ml-shadow`. Existing three Pegasus
  dashboards and the existing Prometheus datasource are preserved. API and
  datasource-proxy checks verify queries; this is not browser visual QA.

## Operations and verification

The new service unit is in `deploy/pegasus/ruby-acorn-ml-shadow.service`.
Install only this unit; do not rerun `install-management.sh`, modify `run.sh`,
restart `ruby-acorn.service`, or load OpenRC into an ML process.

```sh
systemctl status ruby-acorn-ml-shadow
journalctl -u ruby-acorn-ml-shadow --since '-10 minutes' --no-pager
sudo python3 /tmp/verify-ml-runtime.py --expected-autoscaler-pid 56459
```

The expected PID is a receipt from this deployment, not an assumption for a later
session. Check the actual autoscaler identity before choosing it. The runtime
verifier tests file access inside the running ML mount namespace and returns
only sanitized results; credential values are never printed.

To repeat offline evaluation, stop **only** the ML service first so the
single-writer lock is free, then run `evaluate-ml-shadow.sh` as the ML user under
the same resource limits. Reports are overwritten only by an explicitly invoked
evaluation; retain the original receipt before repeating. Live and offline
processes must never share the directory concurrently. Stop/restart operations
on the new ML service do not affect the existing autoscaler.

On monitoring, `install-ml-monitoring.sh` adds the endpoint idempotently,
validates with promtool, snapshots the old config and uses SIGHUP to reload.
It does not replace the existing job, datasource or dashboards. Run
`verify-ml-monitoring.py` as root there for direct and Grafana proxy checks;
existing Grafana credentials are used in memory only.

Sanitized evidence is ignored locally under
`data/pegasus/ml-shadow-20261021/`: offline report with all forecasts and targets,
runtime receipts, monitoring receipts and subsequent verification results.
The old local hourly observer and old history files are untouched.

Validation: 382 local pytest tests passed (four existing OpenStack SDK
deprecation warnings). On Pegasus, 379 passed and three were skipped: the
Windows NTFS test and two SDK contract tests, because the ML environment has no
OpenStack SDK. The tests cover exact horizons, chronological past-only inputs,
shared evaluation targets, zero/gap rejection, freshness/cache handling,
capacity bounds, pending outcomes across restart, and a crash between atomic
state commit and diagnostic journal append. `git diff --check`, systemd unit
validation and promtool validation passed. All 14 new dashboard query targets
were checked directly and through the existing Grafana datasource proxy; all
three legacy dashboards also passed their original verifier. Read-only cloud
inventory confirmed the same six ACTIVE VM identities and no pending tasks.

## Verified live shadow result (2026-10-21, approximately 11:59 Europe/Oslo)

Both actual horizons have matured while the process was running on Pegasus.
The verifier found 42 unique forecasts (no duplicates across controlled ML
restarts), all using already-published origins. Independent reconstruction from
the original source agrees with every recorded live error and cumulative score.

| Model | 15m n | 15m MAE | 15m RMSE | 30m n | 30m MAE | 30m RMSE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| TimesFM | 4 | 1483.83 | 1771.57 | 1 | 711.59 | 711.59 |
| Last value | 4 | 19456.00 | 19464.83 | 1 | 38514.00 | 38514.00 |
| Moving average | 4 | 204851.75 | 204859.77 | 1 | 220705.41 | 220705.41 |

These are **live-issued forecasts of historical replay**, separate from the
197-origin offline result. The few live samples validate the measurement path,
not long-term accuracy. Prometheus and the existing Grafana datasource proxy
both returned all six forecast/capacity series and live MAE/RMSE for both
horizons. All six scrape targets were UP; all 14 new dashboard query targets
passed. Dashboard: `http://127.0.0.1:13000/d/ruby-acorn-ml-shadow` through the
verified existing SSH tunnel. No browser rendering claim is made.

Final original autoscaler PID was still 56459, with zero restarts, fresh
decisions and available input for both games. Installed original service unit
matches its untouched production source. Final read-only cloud inventory again
confirmed the same six ACTIVE VMs/no pending tasks. New ML PID was 60786,
enabled/active with no automatic restarts; verify fresh identity in later
sessions. Last measured ML cgroup memory was 1,128,800,256 bytes, peak
1,129,443,328 bytes, CPU 5.57 seconds since its last controlled restart. RSS peak
during initial loading/evaluation was about 2.0 GiB; normal warm inference about
0.36 seconds. The service remains running under its 2300 MiB / half-core bounds.

Final receipts: `runtime-final.json`, `monitoring-final.json`, `cloud-final.json`,
`source-and-tests.json`, `live-progress.jsonl` and `offline-report.json` under
the ignored evidence directory. No existing VM, pool limit, production strategy,
autoscaler unit or school dashboard was modified; no branch merge was performed.
