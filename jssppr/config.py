from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Input and output
# Put JSSPPR .txt instances in DATASET_DIR. Each execution creates one timestamped
# run directory under RESULTS_DIR.
DATASET_DIR = PROJECT_ROOT / "dataset"
RESULTS_DIR = PROJECT_ROOT / "results"

# SAT model
# SOLVER must be a solver name supported by the installed python-sat package.
SOLVER = "cadical195"

# ENCODING accepts "encoding0", "encoding1", or "encoding1_extra".
ENCODING = "encoding1"

# Preprocessing
# PREPROCESS tightens each operation's start window with job-precedence bounds.
PREPROCESS = True

# Unused preprocessing options
PREPROCESS_FORCED_ORDER = False
PREPROCESS_MAX_PASSES = 5

# Heuristic upper bound
# The heuristic constructs power-feasible schedules before SAT model generation.
# Its best makespan becomes the model horizon and initial UB.
HEURISTIC_UB = True

# False ignores the UB in the dataset and searches through a safe horizon made
# from the complete power timeline plus the duration of every operation.
HEURISTIC_USE_DATASET_UB = False
HEURISTIC_TIME_LIMIT = 1.0
HEURISTIC_SEED = 0
