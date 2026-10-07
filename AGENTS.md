# Project rules

This repository owns one interface: a validated request for an existing `pw.x` input, and a durable Slurm job lifecycle. Read `docs/INTERFACE.md` before changing schemas, state transitions, or remote commands. Read `docs/ERRORS.md` before changing failure handling.

1. Keep the package installable and runnable from this repository alone. All imports must resolve within this package or declared dependencies.
2. Validate request fields, input references, pseudopotential files, and remote path tokens before creating a remote directory. `preview` must have no remote or local state side effects.
3. Preserve one submission per job ID. Write a local record before invoking `sbatch`; treat an ambiguous `sbatch` result as `submission_unknown`. Require an independently verified Slurm ID for `adopt`.
4. Keep Slurm state, QE completion, and SCF convergence as separate facts. Preserve the last known state during accounting lag.
5. Return structured `ToolkitError` failures with stable code and step. Do not silently turn SSH, storage, or parse failures into successful results.
6. Add a focused fake-transport test when changing submission, status, recovery, or fetch behavior. Run `pytest -q` and `ruff check src tests` before considering the change complete.
