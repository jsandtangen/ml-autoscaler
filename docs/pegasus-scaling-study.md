# Offline scaling comparison on Pegasus

Technical run receipt for `feature/pegasus-ml-scaling`, 2026-10-21. This is a
simulation of historical CS:GO, not an intervention in the live VM pool.

## Shared experiment

| Parameter | Value |
| --- | --- |
| Input | Existing source-history.json, appid 730, 69,120 five-minute records |
| Selection | Earliest longest positive, contiguous 300-second segment; selected before forecasting |
| Full clean segment | 2018-04-15 15:50 to 2018-04-20 22:15 UTC; 1,518 observations |
| Warmup | First 64 observations, excluded from totals; no decisions applied during warmup |
| Scored window | 2018-04-15 21:10 to 2018-04-20 21:50 UTC, end exclusive; 120h40m |
| Forecast origins | 1,448, identical for 15m/30m; last six source observations reserved for targets |
| Model | Pretrained TimesFM 2.5; context 64, output steps 3/6; no fine-tuning |
| Initial fleet | One ready VM, no starts pending, timers empty, for every strategy/case |
| VM capacity / price | 100 modeled customer players; assumed EUR 0.10 per active VM-hour |
| Common bounds | Min 0; max 1 or 7; at most one change per decision; 120s cooldown |
| Control clock | Virtual 30-second decisions; five-minute source values held between observations |
| Readiness delay | Assumed 900s and 1800s; actual VM/game-server readiness has not been measured |
| Production assumptions | Customer share 0.0001, shared headroom 0.1 |
| Additional stress | Customer share 0.001, separately labeled; all methods still receive identical demand |

At production share, this segment's peak customer demand is only 48.5248 players,
below the nominal capacity of one VM. Raising the cap alone does not create
workload that benefits from prediction. The separately labeled 10x-share case
tests changing capacity requirements without changing any real allocation.
Both shares are tested at both caps and both startup delays (eight cases).

Strategies: actual `AggressiveCapacityStrategy`, actual `CostOptimizedStrategy`,
`PredictiveCapacityStrategy` with cached 15m forecasts, the same with 30m
forecasts, and `FixedCapacityStrategy` targeting the case's cap. Aggressive
retains its 30% buffer and one spare; cost optimized retains its 110% up/
80% down thresholds and 180s sustained-downscale timer. These are intentional
policy differences, not a common tuned safety margin. A cost difference alone
does not isolate the contribution of forecasting.

## Simulation semantics

- `simulation.py` uses the strategy's returned request. It does not replace an
  aggressive/cost request with the generic CapacityPolicy calculation.
- Contextual strategies receive `desired_for_current` with the simulated current
  allocation and the actual virtual clock. No wall-clock sleep advances timers.
- Starting VMs count toward the common fleet cap, current allocation and billed
  hours immediately; only ready VMs supply player capacity. This prevents
  duplicate starts while capacity is pending. Provider API state transitions,
  errors, uncertain-create handling and synchronous API waits are not emulated.
- Stops take effect immediately and cancel the newest starting VMs first, then
  ready VMs. Disk retention, shutdown/draining delays and stopped-disk charges
  are not modeled. These assumptions apply identically to every strategy.
- Fixed capacity means a fixed **target**, starting from the same one-ready-VM
  state and ramping under the same limits/delay. Its initial ramp is scored too.
- The demand score uses actual modeled customer players versus nominal ready
  player capacity, not versus a strategy's safety-buffer target. Both time below
  capacity and unserved player-hours are retained.
- Warmup never incurs scored cost. At the scored end, capacity/cost accounting
  stops; final pending starts are disclosed and future costs are not invented.

## Cache and fairness checks

The worker computes six forecast steps once per origin and saves steps 3/6.
The append-only cache contains source timestamps and SHA-256 hashes of each
past-only context; its metadata binds source/segment hashes, model revision,
window and common origin range. Missing/changed/duplicate forecasts abort the
study; there is no hidden reactive fallback. Reuse is independent of cap, share,
startup delay and price. The model worker exits, releasing Torch/model memory before replay. No model is instantiated by replay or the auditor.

`scripts/verify_scaling_study.py` independently streams the saved traces,
reconstructs readiness/limits/cooldown and invokes the actual aggressive and
contextual cost implementations again for every 30-second decision. It checks
raw requests, past-only contexts, identical duration, VM-hours conservation,
prices, capacity deficits and scaling counts against the saved summary.

The legacy hourly simulator remains available; its strategy-request overwrite
was fixed too. Existing hourly contract tests remain passing. The new path is
selected with `scripts/simulate_scaling.py --five-minute-study`.

## Isolated Pegasus execution

The new **oneshot**, not boot-enabled, `ruby-acorn-scaling-study.service` is
owned by systemd (PPID 1) and continues independently of SSH/the local PC. Its
ExecStartPost runs the independent auditor automatically. The unit file contains
the complete command used for this experiment.

- Source: `/opt/ruby-acorn-scaling-study`, selected through its own PYTHONPATH.
  Existing autoscaler/shadow source, units and installed packages are untouched.
- Input copy and all results: `/var/lib/ruby-acorn-scaling-study/`;
  experiment subdirectory `20261021/`.
- Reuses the existing CPU-only interpreter and read-only cached model weights;
  no packages are installed in the active environment.
- User `ruby-acorn-ml`, no OS_* environment, home/production/shadow-state paths
  inaccessible, no capabilities or privilege escalation. Network is denied and
  address families restricted to AF_UNIX. No OpenStack access or infrastructure
  controller exists in the replay path.
- CPUQuota 50% of one core, Nice 15, MemoryHigh 1300 MiB, MemoryMax 1500 MiB,
  no swap. Measured while forecasting: about 1.1 GB cgroup memory with over
  1.1 GiB guest RAM still available. Cgroup accounting and process peak RSS are
  different; cached model pages may be shared.

Files: `study.json` (all assumptions/results), `summary.csv` (all methods/cases),
`forecasts.jsonl`, `forecast-metadata.json`, `forecast-generation.json`,
`audit.json`, and a report plus compressed 30-second decision CSV in each case
directory. Logs: `journalctl -u ruby-acorn-scaling-study`.

Completed results are not overwritten. To repeat, use a new output directory
and a matching copy of the cache/metadata with `--study-phase replay`; do not
restart the completed unit against its existing output path. No cleanup or live
configuration change is implied by this experiment.

## Limitations

This is one clean historical segment chosen on quality/length, not on model
scores. Model pretraining overlap is unknown. Price, customer share, player
capacity and readiness delays are assumptions. Management/monitoring, ML
compute, disks and actual billing are excluded. The higher cap is not a quota
request or proof of deployed game-server capacity. Any advantage is conditional
on this workload and the capacity/readiness assumptions.

## Results

All currency values are modeled EUR; VM-hours include starting VMs. Under-capacity
time counts any nominal shortfall, even a fraction of one modeled player.

### production_assumptions, 15-minute readiness delay

| Cap | Strategy | EUR | Saved vs fixed EUR | VM-hours | Below capacity hours | Unserved player-hours | Up / down |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | aggressive_capacity | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 1 | cost_optimized | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 1 | timesfm_15m | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 1 | timesfm_30m | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 1 | fixed_capacity | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 7 | aggressive_capacity | 24.13 | 60.28 | 241.33 | 0.00 | 0.00 | 1 / 0 |
| 7 | cost_optimized | 12.07 | 72.35 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 7 | timesfm_15m | 12.07 | 72.35 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 7 | timesfm_30m | 12.07 | 72.35 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 7 | fixed_capacity | 84.42 | 0.00 | 844.17 | 0.00 | 0.00 | 6 / 0 |

### simulated_load_stress, 15-minute readiness delay

| Cap | Strategy | EUR | Saved vs fixed EUR | VM-hours | Below capacity hours | Unserved player-hours | Up / down |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | aggressive_capacity | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 1 | cost_optimized | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 1 | timesfm_15m | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 1 | timesfm_30m | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 1 | fixed_capacity | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 7 | aggressive_capacity | 57.83 | 26.58 | 578.33 | 0.32 | 56.79 | 25 / 20 |
| 7 | cost_optimized | 37.04 | 47.38 | 370.36 | 22.00 | 445.59 | 14 / 10 |
| 7 | timesfm_15m | 39.86 | 44.56 | 398.57 | 0.32 | 56.79 | 20 / 17 |
| 7 | timesfm_30m | 39.90 | 44.52 | 398.98 | 0.40 | 56.80 | 20 / 17 |
| 7 | fixed_capacity | 84.42 | 0.00 | 844.17 | 0.32 | 56.79 | 6 / 0 |

### production_assumptions, 30-minute readiness delay

| Cap | Strategy | EUR | Saved vs fixed EUR | VM-hours | Below capacity hours | Unserved player-hours | Up / down |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | aggressive_capacity | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 1 | cost_optimized | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 1 | timesfm_15m | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 1 | timesfm_30m | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 1 | fixed_capacity | 12.07 | 0.00 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 7 | aggressive_capacity | 24.13 | 60.28 | 241.33 | 0.00 | 0.00 | 1 / 0 |
| 7 | cost_optimized | 12.07 | 72.35 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 7 | timesfm_15m | 12.07 | 72.35 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 7 | timesfm_30m | 12.07 | 72.35 | 120.67 | 0.00 | 0.00 | 0 / 0 |
| 7 | fixed_capacity | 84.42 | 0.00 | 844.17 | 0.00 | 0.00 | 6 / 0 |

### simulated_load_stress, 30-minute readiness delay

| Cap | Strategy | EUR | Saved vs fixed EUR | VM-hours | Below capacity hours | Unserved player-hours | Up / down |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | aggressive_capacity | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 1 | cost_optimized | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 1 | timesfm_15m | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 1 | timesfm_30m | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 1 | fixed_capacity | 12.07 | 0.00 | 120.67 | 120.67 | 18863.50 | 0 / 0 |
| 7 | aggressive_capacity | 57.83 | 26.58 | 578.33 | 0.53 | 105.41 | 25 / 20 |
| 7 | cost_optimized | 37.04 | 47.38 | 370.36 | 25.20 | 616.06 | 14 / 10 |
| 7 | timesfm_15m | 39.86 | 44.56 | 398.57 | 0.53 | 105.41 | 20 / 17 |
| 7 | timesfm_30m | 39.90 | 44.52 | 398.98 | 0.62 | 105.42 | 20 / 17 |
| 7 | fixed_capacity | 84.42 | 0.00 | 844.17 | 0.53 | 105.41 | 6 / 0 |

## Findings and verification

- At production share and cap 1, all five methods stay at one VM: EUR 12.07,
  120.67 VM-hours, no capacity shortfall and no scaling events. There is no
  demonstrated ML scaling advantage for this workload/configuration.
- Raising only the cap to 7 leaves cost_optimized and both ML methods identical
  at one VM. Aggressive's spare asks for two, while fixed ramps to seven. This
  cost difference comes from policy, not a demonstrated need for prediction.
- With 10x share, cap 1 is saturated throughout: every method has 120.67 hours
  below capacity. Forecast accuracy cannot overcome the fleet cap.
- With 10x share, cap 7 and 15m startup, TimesFM15 costs 31.08% less than
  aggressive, with the same 19 minutes / 56.79 player-hours of shortfall.
  For TimesFM15, aggressive and fixed, that shortfall occurs entirely during
  the common initial ramp; there is none afterward.
- Relative to cost_optimized, TimesFM15 costs 7.62% more (EUR 2.82) but lowers
  shortfall time from 22 hours to 19 minutes. Cost_optimized deliberately
  tolerates modeled utilization above 100%, so this is a policy tradeoff too.
- TimesFM30 is not better than TimesFM15 here. Its extra five minutes of
  shortfall correspond to only about 0.009 additional unserved player-hours;
  counting shortfall duration alone exaggerates this tiny threshold crossing.
- With 30m startup, TimesFM15/aggressive/fixed have 32 minutes of shortfall,
  again from the shared initial ramp; cost_optimized has 25.2 hours. These
  conditional results do not establish a general benefit of one horizon.

395 local tests passed (four existing SDK deprecation warnings); Pegasus:
392 passed, three expected skips (Windows and SDK contracts). The independent
post-job auditor passed all 579,200 decision rows, and passed again locally
using only saved forecasts and the copied source data, without loading TimesFM.
Forecast generation made 1,448 six-step calls, took 561.49s wall / 280.77s CPU,
and recorded peak process RSS 2,103,869,440 bytes. The complete unit, including
replay and audit, took about ten minutes / 298.32s CPU; journal reports 1.0G
cgroup peak, no swap. During inference, over 1.1 GiB RAM remained available.
Process RSS includes shared file pages and differs from cgroup accounting.

The job ended successfully/inactive; it is not boot-enabled. Original autoscaler
PID 56459 and ML shadow PID 60786 remained active with zero restarts. Final
Prometheus/Grafana checks passed, both live shadow horizons were still scored,
and a read-only inventory confirmed the same six ACTIVE VMs with no tasks.
No existing service/unit/source/environment or VM was changed; no merge occurred.

Local ignored artifacts: `data/pegasus/scaling-study-20261021/`. Results and input
copy are under `results/`; isolation, monitoring/cloud receipts and a second local
audit are adjacent. The server retains the complete input/cache/results under
`/var/lib/ruby-acorn-scaling-study/`. Source changes remain on the ML branch.
