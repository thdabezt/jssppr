from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files
DATASET_DIR = PROJECT_ROOT / "dataset"
RESULTS_DIR = PROJECT_ROOT / "results"

# Backend: "sat", "cplex_cp", "cplex_mip", or "gurobi".
# CPLEX backends require IBM CPLEX Optimization Studio. Gurobi requires a full
# licence; SAT requires python-sat and pypblib.
BACKEND = "sat"

# SAT
SOLVER = "cadical195"

# True restricts operation starts to precedence-based [ES, LS] windows.
PREPROCESS = True

# Horizon:
#   "heuristic" uses Algorithm 1 and its verified schedule as a warm start.
#   "cache" reads fixed bounds from UB_CACHE_FILE for comparable runs.
#   "safe" uses no initial upper bound or warm start.
#   "dataset" trusts the bound stored in each instance file.
HORIZON = "heuristic"
UB_CACHE_FILE = PROJECT_ROOT / "initial_ub_cache.txt"
HEURISTIC_TIME_LIMIT = 5.0
HEURISTIC_SEED = 0

# Per-instance wall-clock limit in seconds; None disables it.
TIME_LIMIT_SECONDS: Optional[float] = 3600.0

# CP Optimizer
CPLEX_CP_WORKERS = "Auto"
CPLEX_CP_SEARCH_TYPE = "Auto"
CPLEX_CP_LOG_VERBOSITY = "Quiet"
CPLEX_CP_EXECFILE: Optional[str] = None

# CPLEX MILP
CPLEX_MIP_THREADS = 0
CPLEX_MIP_RELATIVE_GAP = 0.0
CPLEX_MIP_ABSOLUTE_GAP = 0.0
CPLEX_MIP_RANDOM_SEED = 0
CPLEX_MIP_LOG_OUTPUT = False

# Gurobi MILP
GUROBI_THREADS = 0
GUROBI_RELATIVE_GAP = 0.0
GUROBI_ABSOLUTE_GAP = 0.0
GUROBI_RANDOM_SEED = 0
GUROBI_LOG_OUTPUT = False
