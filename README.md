# jssppr

## Experimental environment

```
VM type:          Google Cloud c4-highmem-4 (Ubuntu 22.04.5 LTS, kernel 6.8.0-1064-gcp)
CPU:              Intel Xeon Platinum 8581C @ 2.30 GHz - 4 vCPU (2 cores, 2 threads/core)
RAM:              30.4 GB
Timeout:          3600 s per instance (CPLEX / Gurobi)
Cadical version:  CaDiCaL 1.9.5 (python-sat 1.9.dev4, solver "cadical195")
CPLEX version:    22.2.0.0 (CP Optimizer 22.2.0.0; docplex 2.32.264)
Gurobi version:   13.0.2
```

The timeout applies to the CP Optimizer, CPLEX and Gurobi backends. The SAT
backend has no time limit: it runs the incremental loop until UNSAT, so every
SAT result is a completed optimality proof.
