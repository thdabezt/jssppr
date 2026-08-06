from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Input and output
# Put JSSPPR .txt instances in DATASET_DIR. Each execution creates one timestamped
# run directory under RESULTS_DIR.
DATASET_DIR = PROJECT_ROOT / "dataset"
RESULTS_DIR = PROJECT_ROOT / "results"

# Backend
# BACKEND selects the model that is built and solved. Every backend reads the
# same instance file, uses the same heuristic upper bound as its horizon, and
# verifies the schedule it decodes with the same checker, so their makespans are
# directly comparable.
#   "sat"       incremental SAT / Pseudo-Boolean model, solved with python-sat.
#   "cplex_cp1" IBM CP Optimizer interval model, a direct conversion of the SAT
#               model: one interval per operation, one no-overlap per machine,
#               and one cumulative power function.
#   "cplex_cp2" IBM CP Optimizer model structured like the 2017 paper: operations
#               expanded into constant-power suboperations, threshold changes
#               expanded into fixed dummy operations, and the reusable power flow
#               projected onto a single cumulative resource.
#   "cplex_mip" IBM CPLEX MILP, equations (1)-(14) of the 2017 paper.
#   "gurobi"    the same equations (1)-(14) solved with Gurobi. It is built from
#               the same operation expansion as "cplex_mip", so the two solve an
#               identical model and their results are directly comparable.
# "cplex_cp1", "cplex_cp2", and "cplex_mip" need IBM CPLEX Optimization Studio
# and its Python packages; "gurobi" needs gurobipy and a Gurobi license. None of
# them are required to run "sat".
BACKEND = "sat"

# SAT model
# SOLVER must be a solver name supported by the installed python-sat package.
# The encoding follows section 4.3 of the paper. It builds the order variables
# X and S, the execution-state variables Peak and Base, equations (10)-(16) and
# (21), and the job-precedence, machine-no-overlap, and makespan-bound clauses.
# Equations (17)-(20), the extra order/state consistency clauses, are not built.
# Equation (21) is always translated by the Binary Merger pseudo-Boolean
# encoding through pypblib. This is deliberately not configurable: a different
# translation produces a different CNF, and clause counts and solving times are
# only comparable against the encoding they were produced with.
SOLVER = "cadical195"

# Preprocessing
# PREPROCESS builds the start-time window [ES, LS] of every operation from
# equations (1)-(3): ES from the processing times earlier in the job, LS from
# the horizon minus the remaining processing time of the job. Boolean variables
# are then generated only for start times inside that window.
# False falls back to the untightened window [0, H - P] and leaves every
# scheduling decision to the solver.
PREPROCESS = True

# Horizon
# HORIZON selects the scheduling horizon H, which is also the initial makespan
# bound. Every backend uses the same H, so runs are only comparable against
# other runs made with the same setting.
#   "heuristic" runs Algorithm 1 and takes its makespan as H. The schedule it
#               found is verified before use, which makes H a horizon that is
#               known to admit a feasible schedule, and that schedule is handed
#               to the CPLEX backends as a warm start.
#   "cache"     reads H for each instance from UB_CACHE_FILE instead of running
#               Algorithm 1 for it. Every backend then builds its model over the
#               same fixed horizon on every run, which is what makes model sizes
#               and solving times comparable across backends and across machines.
#               Algorithm 1 still runs to produce a warm start for the CPLEX and
#               Gurobi backends, and that warm start is used only when its
#               makespan does not exceed the cached H.
#   "safe"      skips the heuristic: H is the last power-threshold change plus
#               the total processing time of every operation, and the CPLEX
#               backends start with no warm start. Use this to measure a backend
#               that is given no upper bound. H is roughly three to seven times
#               the optimum on the Kemmoe et al. instances, which barely affects
#               the CP and MIP models because their size does not depend on H,
#               but multiplies the size of the time-indexed SAT model.
#   "dataset"   uses the horizon declared in the instance file. On the Kemmoe
#               et al. instances shipped in dataset/ that value is below the
#               proven optimum for most instances, so this setting reports
#               infeasible rather than a worse schedule. It is kept only for
#               instance files whose declared horizon is a genuine upper bound.
HORIZON = "heuristic"

# Upper bounds used when HORIZON is "cache". One entry per line, written as
#     instance_file_name:upper_bound
# Instances missing from the file are rejected rather than silently falling back
# to another horizon, so a cached run cannot mix horizon sources.
UB_CACHE_FILE = PROJECT_ROOT / "initial_ub_cache.txt"

# Heuristic upper bound, used when HORIZON is "heuristic", and used for the
# CPLEX and Gurobi warm start when HORIZON is "cache"
# Algorithm 1 of the paper. The heuristic repeatedly builds a schedule by
# picking a job with an unscheduled operation at random and placing its next
# operation at the earliest start time allowed by job precedence, machine
# availability, and the power threshold, keeping the smallest makespan found.
# HEURISTIC_SEED fixes the random choices, but the number of restarts is decided
# by HEURISTIC_TIME_LIMIT in wall-clock time, so a faster machine completes more
# restarts and can return a smaller bound. The bound is therefore reproducible
# on one machine under one load, not across machines. Use HORIZON = "cache" when
# the horizon has to be identical everywhere.
HEURISTIC_TIME_LIMIT = 5.0
HEURISTIC_SEED = 0

# Search limit
# TIME_LIMIT_SECONDS bounds the search for one instance, and None removes the
# limit. Reaching it returns the best incumbent found so far instead of a proof,
# which is reported as optimal=False.
# CP Optimizer, CPLEX, and Gurobi take it as a native solver parameter. PySAT
# cannot interrupt CaDiCaL from inside the process, so the SAT backend instead
# solves each instance in a child process that is stopped at the limit. The
# child rewrites its result after every improving makespan, so the best schedule
# found so far survives being stopped and is reported with stop_cause
# "time limit". A stopped SAT run is never marked optimal, because the UNSAT
# proof it was working on never finished.
TIME_LIMIT_SECONDS: Optional[float] = 3600.0

# IBM CP Optimizer
# Used by "cplex_cp1" and "cplex_cp2". CPLEX_CP_EXECFILE points at the
# cpoptimizer executable; None lets DOcplex locate it on PATH.
CPLEX_CP_WORKERS = "Auto"
CPLEX_CP_SEARCH_TYPE = "Auto"
CPLEX_CP_LOG_VERBOSITY = "Quiet"
CPLEX_CP_EXECFILE: Optional[str] = None

# IBM CPLEX MIP
# Used by "cplex_mip". Zero gaps request proof of the integer optimum; with a
# TIME_LIMIT_SECONDS the search can still return the best feasible incumbent.
CPLEX_MIP_THREADS = 0
CPLEX_MIP_RELATIVE_GAP = 0.0
CPLEX_MIP_ABSOLUTE_GAP = 0.0
CPLEX_MIP_RANDOM_SEED = 0
CPLEX_MIP_LOG_OUTPUT = False

# Gurobi
# Used by "gurobi". Threads 0 lets Gurobi choose. Zero gaps request proof of the
# integer optimum. The model is larger than the size-limited license that ships
# with the gurobipy wheel allows, so a full license must be installed.
GUROBI_THREADS = 0
GUROBI_RELATIVE_GAP = 0.0
GUROBI_ABSOLUTE_GAP = 0.0
GUROBI_RANDOM_SEED = 0
GUROBI_LOG_OUTPUT = False
