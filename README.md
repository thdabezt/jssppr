# JSSPPR: Exact SAT and Constraint Programming for the Job Shop Scheduling Problem with Power Requirements

This repository provides the Python source code, benchmark instances and
certified results for the paper **"Exact SAT and Constraint Programming for Job
Shop Scheduling with Time-Varying Peak Power Constraints"**

---

## Prerequisites and Dependencies

* **Python:** 3.10 or newer.


* **SAT backend:** `python-sat` and `pypblib`, installed from
  `requirements.txt`. The default solver is CaDiCaL 1.9.5, shipped with
  `python-sat` as `cadical195`.


* **CP and CPLEX MILP backends:** IBM CPLEX Optimization Studio. `docplex` is on
  PyPI, while the `cplex` package and the `cpoptimizer` executable come from the
  Studio installation. Set `CPLEX_CP_EXECFILE` in `jssppr/config.py` if
  `cpoptimizer` is not on `PATH`.


* **Gurobi backend:** `gurobipy` plus a full licence. The one bundled with the
  wheel is limited to 2000 variables and 2000 constraints, which every instance
  exceeds.

The SAT backend alone reproduces the certified optimal makespans; the others are
only needed for the comparison.

---

## Installation

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

```

---

## Usage

```bash
python -m jssppr [OPTIONS] [instances ...]

```

Without positional arguments the whole dataset directory is solved. Every option
defaults to the value set in `jssppr/config.py`.

### Command-Line Arguments

* `instances`: instance files to solve. Defaults to every `.txt` file in the
  dataset directory.


* `-b, --backend {sat,cplex_cp,cplex_mip,gurobi}`: solving backend. Default is
  `sat`.


* `-s, --solver NAME`: PySAT solver used by the SAT backend.


* `-H, --horizon {heuristic,cache,fixed,safe,dataset}`: how the initial upper
  bound is obtained. `heuristic` runs the randomised construction and warm-starts
  from it (default), `cache` reads `published_upper_bounds.txt`, `fixed` uses
  `--upper-bound`, `safe` starts without a bound, `dataset` trusts the bound in
  the instance file.


* `-u, --upper-bound CMAX`: fixed initial upper bound. Implies `--horizon fixed`.


* `-t, --time-limit SECONDS`: per-instance wall-clock limit. Default is 3600;
  `none` disables it. The SAT backend keeps its latest verified incumbent when
  the limit is reached.


* `--heuristic-time-limit SECONDS`: time given to the upper-bound heuristic,
  5 by default, charged to the reported solving time.


* `--seed SEED`: random seed of that heuristic. Default is 0.


* `--preprocess` / `--no-preprocess`: restrict operation starts to the
  precedence-based `[ES, LS]` windows, or keep the full domains. On by default.


* `-d, --dataset-dir DIR` / `-o, --results-dir DIR`: input and output
  directories.

### Example

Prove optimality for one instance with the SAT backend within one hour:

```bash
python -m jssppr -b sat -t 3600 dataset/JSPPR_4_10x4.txt

```

Use `-H cache` to start from the published bounds, which is what reproduces the
reported `UB`, `vars` and `clauses` values. The default `heuristic` mode searches
for a fixed amount of wall-clock time, so its bound depends on the machine.

---

## Output and Statistics

Each invocation creates `results/run_<timestamp>_<backend>/`, holding a
`summary.csv` and one directory per instance with `result.json`, `data.txt`,
`log.txt`, `schedule.txt` and `raw_schedule.json`. The reported statistics are:

* `makespan` / `optimal`: verified makespan of the best schedule, and whether
  optimality was proven.


* `best_bound` / `gap`: best lower bound and relative gap.


* `solve_time`: total wall-clock time, covering reading the instance, the
  upper-bound procedure, preprocessing and the search.


* `time_first_sat` / `time_latest_sat` / `time_unsat`: elapsed time at the first
  and last incumbent, and the time spent on the closing unsatisfiability proof.


* `vars` / `clauses`: model size. Interval variables and constraints for CP,
  columns and rows for the MILP models.


* `UB` / `horizon_mode`: initial upper bound, which is also the horizon of the
  encoded model, and how it was obtained.

Every schedule is checked against the precedence, machine-capacity and power
constraints before it is reported, so a row with `optimal = True` carries an
independently verified certificate.

---

## Results

`results/` holds the published results, one directory per backend with the same
shape as a run directory, plus the historical results of Kemmoe et al. (2017):

```
results/<backend>/summary.csv
results/<backend>/<instance>/{data.txt,schedule.txt,raw_schedule.json}
results/<backend>/schedules/<instance>.out
results/kemmoe2017/{summary.csv,schedules/,figures/}

```

The `.out` files are not written by the solver. They are generated from the run
output in the format of the schedule files distributed with the original
benchmark, so the two can be compared directly.

| Method | #OPTIMAL | #BEST | Total time (s) |
| --- | --- | --- | --- |
| SAT | 35 | 35 | 2,672.55 |
| CPLEX CP | 35 | 35 | 263.11 |
| CPLEX MILP | 6 | 13 | 104,813.45 |
| Gurobi | 10 | 13 | 91,686.53 |
| CPLEX MILP (Kemmoe et al., 2017) | – | 16 | – |
| GRASP × ELS (Kemmoe et al., 2017) | – | 24 | – |

The counts follow the definitions used in the paper. Total time sums the
measured `solve_time` values, so an instance that reaches the 3600-second limit
counts slightly above it; counting those at the limit gives 104,636.30 s for
CPLEX MILP and 91,550.62 s for Gurobi.

SAT and CP prove optimality on all 35 instances and agree on every makespan,
improving four previously reported values. Seven historical makespans fall below
the proven optimum and are marked `consistent = False` in
`results/kemmoe2017/summary.csv`.

---

## Verifying a Schedule

`tools/verify_schedules.py` checks `.out` schedules against the instance data,
independently of the solver that produced them. Start times and machine routes
are taken as given; routing order, machine capacity and `P(t) <= PT(t)` are
re-derived.

```bash
python tools/verify_schedules.py --strict results/sat/schedules

```

`--strict` exits non-zero if any schedule is infeasible. `--figures DIR` also
plots machine occupancy and the power profile for each rejected schedule, which
needs `matplotlib`; `--figures-all` covers the feasible ones too.

All 137 published schedules pass. The same command on
`results/kemmoe2017/schedules` reports nine historical schedules that exceed the
power threshold, with the plots in `results/kemmoe2017/figures`.

---

## Experimental environment

```
VM type:          Google Cloud c4-highmem-4 (Ubuntu 22.04.5 LTS, kernel 6.8.0-1064-gcp)
CPU:              Intel Xeon Platinum 8581C @ 2.30 GHz - 4 vCPU (2 cores, 2 threads/core)
RAM:              31 GB
Timeout:          3600 s per instance
Cadical version:  CaDiCaL 1.9.5 (python-sat 1.9.dev4, solver "cadical195")
CPLEX version:    22.2.0.0 (CP Optimizer 22.2.0.0; docplex 2.32.264)
Gurobi version:   13.0.2
```
