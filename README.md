# QE HPC Toolkit

**English** | [简体中文](README.zh-CN.md)

Submit an existing Quantum ESPRESSO `pw.x` input to a Slurm cluster, check its status, and retrieve its output. The toolkit checks that the input and pseudopotential files agree, records each submission, and prevents accidental reuse of a job ID.

## Quick start

The examples use Windows PowerShell. You need Python 3.11 or newer, local `ssh` and `scp` commands, SSH access to a Slurm cluster, and `pw.x` available on the cluster's compute nodes.

### 1. Install

```powershell
git clone https://github.com/Syanz7755/qe-hpc-toolkit.git
Set-Location .\qe-hpc-toolkit
python -m pip install -e .
```

### 2. Prepare one calculation

Copy the request template and create a directory for pseudopotentials:

```powershell
Copy-Item .\examples\request.example.json .\request.json
New-Item -ItemType Directory -Force .\pseudo | Out-Null
```

Place your **reviewed** QE input in `input.in` and the actual `.UPF` files it names in `pseudo/`:

```text
qe_hpc_toolkit/
├── request.json
├── input.in
└── pseudo/
    ├── Mg.upf
    └── O.upf
```

Edit `request.json` for your calculation:

- `cluster.host`: an alias from your local SSH config, such as `my-cluster`. Confirm that `ssh my-cluster` works without an interactive password prompt.
- `cluster.remote_root`: an absolute cluster path where you can write jobs, such as `/scratch/your-name/qe-jobs`.
- `job.job_id`: a unique name for this calculation. Use a new ID for every new calculation.
- `job.calculation` and `job.resources`: match the calculation in `input.in` and your cluster's partition and resource requirements.
- `job.pseudopotentials`: list every pseudopotential named in the input's `ATOMIC_SPECIES` section. Replace the template's Mg/O files for your own system.

Set `cluster.modules` to the modules your cluster requires, or to `[]` if none are needed. Choose the appropriate `mpi_launcher` (`srun` or `mpirun`) and `qe_executable`. In `input.in`, set `pseudo_dir = './pseudo'` and `outdir = './outdir'`. The included [sample input](examples/input.in) shows the required layout; it is not a scientifically validated input for your calculation.

### 3. Preview, then submit

```powershell
qe-hpc preview --config .\request.json
```

`preview` runs locally. It shows file SHA-256 hashes, the remote directory, and the complete Slurm script. Check the input, pseudopotentials, partition, CPU count, memory, time limit, and `pw.x` command before submitting:

```powershell
qe-hpc submit --config .\request.json
```

The returned `slurm_job_id` is the cluster job number. Keep the original `request.json`, `input.in`, and pseudopotential files: later commands verify that they still match the submitted job.

### 4. Check status and fetch results

```powershell
qe-hpc status --config .\request.json
```

Repeat as needed. Once the state is `completed`, `failed`, `cancelled`, or `timeout`, fetch the available output:

```powershell
qe-hpc fetch --config .\request.json --destination .\artifacts
```

Files appear in `artifacts/<job_id>/`. `output.out` is the QE output; `stdout.log` and `stderr.log` are job logs when available. `result.json` reports the `JOB DONE.` marker, SCF convergence marker, and last total energy. **Slurm's `completed` state only means the process ended**; inspect the QE output to assess the calculation. Large intermediate files in `outdir/` stay on the cluster.

## If submission status is uncertain

If SSH times out during submission, the local state may become `submission_unknown`. Check the cluster for an existing job first. If you find it, record its real Slurm ID:

```powershell
qe-hpc adopt --config .\request.json --slurm-id 12345
qe-hpc status --config .\request.json
```

`submit` never retries an existing `job_id` automatically. Records default to `.qe-hpc/` under your current directory. Run later commands from the same directory, or pass the same `--state-dir` each time. See the [error guide](docs/ERRORS.md) (Chinese) for recovery details.

## Interface and development

The [interface contract](docs/INTERFACE.md) (Chinese) describes request fields and states. Run `qe-hpc schema` for the full JSON Schema. The toolkit runs prepared `pw.x` inputs; it does not generate atomic structures or scientific parameters.

For development:

```powershell
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests
```

Repository rules are in [AGENTS.md](AGENTS.md).
