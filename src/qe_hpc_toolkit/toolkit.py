"""QE input validation, Slurm lifecycle, durable records, and output parsing."""

import hashlib
import json
import os
import re
import shlex
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .errors import ToolkitError
from .models import JobRecord, JobState, QEParseResult, SubmissionPlan, SubmissionRequest
from .transport import OpenSSHTransport, Transport

RY_TO_EV = 13.605693122994
_TERMINAL = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED, JobState.TIMEOUT}
_PSEUDO_NAME = re.compile(r"^[A-Za-z0-9_.+-]+\.[Uu][Pp][Ff]$")
_ENERGY = re.compile(r"!?\s*total energy\s*=\s*([-+]?\d+(?:\.\d+)?(?:[EeDd][-+]?\d+)?)\s+Ry", re.I)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_file(path: Path, step: str) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolkitError("INPUT_UNAVAILABLE", step, f"cannot read {path}: {exc}") from exc
    if not data:
        raise ToolkitError("INPUT_INVALID", step, f"empty file: {path}")
    return data


def _validate_qe_input(data: bytes, pseudo_names: set[str], calculation: str) -> None:
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolkitError("INPUT_INVALID", "validate", "QE input must be UTF-8") from exc
    required = ["&CONTROL", "&SYSTEM", "ATOMIC_SPECIES", "ATOMIC_POSITIONS", "K_POINTS"]
    for section in required:
        if not re.search(rf"(?im)^\s*{re.escape(section)}\b", source):
            raise ToolkitError("INPUT_INVALID", "validate", f"missing {section} section")
    for field, expected in (("pseudo_dir", "pseudo"), ("outdir", "outdir")):
        match = re.search(rf"(?im)^\s*{field}\s*=\s*['\"]([^'\"]+)['\"]", source)
        if not match or match.group(1).removeprefix("./") != expected:
            raise ToolkitError("INPUT_INVALID", "validate", f"{field} must be './{expected}'")
    actual = re.search(r"(?im)^\s*calculation\s*=\s*['\"]([^'\"]+)['\"]", source)
    if not actual or actual.group(1).lower() != calculation:
        raise ToolkitError(
            "INPUT_INVALID", "validate", f"calculation must match request: {calculation}"
        )
    header = re.search(r"(?im)^[ \t]*ATOMIC_SPECIES[ \t]*\r?$", source)
    assert header is not None
    species = source[header.end() :].splitlines()
    referenced: set[str] = set()
    for line in species:
        if not line.strip() and not referenced:
            continue
        fields = line.split()
        if len(fields) != 3 or not re.fullmatch(r"[A-Z][a-z]?", fields[0]):
            break
        try:
            float(fields[1])
        except ValueError as exc:
            raise ToolkitError("INPUT_INVALID", "validate", "invalid ATOMIC_SPECIES mass") from exc
        referenced.add(fields[2])
    if not referenced:
        raise ToolkitError("INPUT_INVALID", "validate", "ATOMIC_SPECIES has no entries")
    missing = referenced - pseudo_names
    if missing:
        raise ToolkitError(
            "PSEUDO_MISSING", "validate", f"missing pseudopotentials: {', '.join(sorted(missing))}"
        )


def _render_script(request: SubmissionRequest, remote_dir: str) -> str:
    cluster, job = request.cluster, request.job
    resources = job.resources
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job.job_id}",
        f"#SBATCH --partition={resources.partition}",
        f"#SBATCH --ntasks={resources.mpi_ranks}",
        f"#SBATCH --cpus-per-task={resources.threads_per_rank}",
        f"#SBATCH --mem={resources.memory}",
        f"#SBATCH --time={resources.walltime}",
        f"#SBATCH --output={remote_dir}/stdout.log",
        f"#SBATCH --error={remote_dir}/stderr.log",
    ]
    if resources.account:
        lines.append(f"#SBATCH --account={resources.account}")
    lines.extend(["set -euo pipefail", f"cd {shlex.quote(remote_dir)}"])
    lines.extend(f"module load {shlex.quote(name)}" for name in cluster.modules)
    lines.extend(
        [
            "mkdir -p outdir",
            f"export OMP_NUM_THREADS={resources.threads_per_rank}",
        ]
    )
    launcher = f"{cluster.mpi_launcher} -n {resources.mpi_ranks}"
    lines.append(f"{launcher} {shlex.quote(cluster.qe_executable)} -in input.in > output.out")
    return "\n".join(lines) + "\n"


def parse_qe_output(job_id: str, output: bytes | None) -> QEParseResult:
    if output is None:
        return QEParseResult(job_id=job_id, warnings=["output.out is missing"])
    content = output.decode("utf-8", errors="replace")
    energies = _ENERGY.findall(content)
    energy = float(energies[-1].replace("D", "E").replace("d", "e")) if energies else None
    done = bool(re.search(r"(?m)^\s*JOB DONE\.\s*$", content))
    converged = "convergence has been achieved" in content.lower()
    warnings = []
    if not done:
        warnings.append("QE JOB DONE marker is absent")
    if not converged:
        warnings.append("SCF convergence marker is absent")
    if energy is None:
        warnings.append("total energy was not found")
    return QEParseResult(
        job_id=job_id,
        output_sha256=_sha256(output),
        job_done=done,
        convergence_achieved=converged,
        total_energy_ry=energy,
        total_energy_ev=energy * RY_TO_EV if energy is not None else None,
        warnings=warnings,
    )


class QEHpcToolkit:
    """One job per ID. Preview is read-only; submit is an explicit side effect."""

    def __init__(self, state_dir: Path, transport: Transport | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.transport = transport

    def preview(self, request: SubmissionRequest) -> SubmissionPlan:
        input_data = _read_file(request.job.input_file, "validate")
        hashes: dict[str, str] = {}
        for path in request.job.pseudopotentials:
            if not _PSEUDO_NAME.fullmatch(path.name):
                raise ToolkitError(
                    "INPUT_INVALID", "validate", f"invalid pseudopotential filename: {path.name}"
                )
            hashes[path.name] = _sha256(_read_file(path, "validate"))
        _validate_qe_input(input_data, set(hashes), request.job.calculation)
        remote_dir = f"{request.cluster.remote_root.rstrip('/')}/{request.job.job_id}"
        request_fields = request.model_dump(mode="json")
        request_fields["cluster"].pop("connect_timeout_seconds")
        request_fields["cluster"].pop("operation_timeout_seconds")
        payload = {
            "request": request_fields,
            "input_sha256": _sha256(input_data),
            "pseudopotential_sha256": hashes,
        }
        fingerprint = _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        return SubmissionPlan(
            job_id=request.job.job_id,
            request_sha256=fingerprint,
            remote_dir=remote_dir,
            input_sha256=_sha256(input_data),
            pseudopotential_sha256=hashes,
            script=_render_script(request, remote_dir),
        )

    def _transport(self, request: SubmissionRequest) -> Transport:
        return self.transport or OpenSSHTransport(
            request.cluster.host,
            request.cluster.connect_timeout_seconds,
            request.cluster.operation_timeout_seconds,
        )

    def _record_path(self, job_id: str) -> Path:
        return self.state_dir / f"{job_id}.json"

    def _load(self, job_id: str) -> JobRecord:
        path = self._record_path(job_id)
        if not path.exists():
            raise ToolkitError("JOB_NOT_FOUND", "record", f"no local record for {job_id}")
        try:
            return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolkitError("RECORD_INVALID", "record", f"cannot read {path}: {exc}") from exc

    def _save(self, record: JobRecord) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            record.updated_at = datetime.now(UTC)
            descriptor, temp_path = tempfile.mkstemp(
                prefix=f".{record.job_id}.", dir=self.state_dir
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(record.model_dump_json(indent=2))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, self._record_path(record.job_id))
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        except OSError as exc:
            raise ToolkitError("RECORD_IO_FAILED", "record", str(exc)) from exc

    @contextmanager
    def _submit_lock(self, job_id: str) -> Iterator[None]:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolkitError("RECORD_IO_FAILED", "submit", str(exc)) from exc
        path = self.state_dir / f"{job_id}.lock"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ToolkitError(
                "JOB_BUSY", "submit", f"job {job_id} has an active submission lock"
            ) from exc
        except OSError as exc:
            raise ToolkitError("RECORD_IO_FAILED", "submit", str(exc)) from exc
        try:
            os.close(descriptor)
            yield
        finally:
            path.unlink(missing_ok=True)

    def submit(self, request: SubmissionRequest) -> JobRecord:
        plan = self.preview(request)
        with self._submit_lock(plan.job_id):
            if self._record_path(plan.job_id).exists():
                existing = self._load(plan.job_id)
                raise ToolkitError(
                    "JOB_EXISTS",
                    "submit",
                    f"job {plan.job_id} already has a {existing.state.value} record; "
                    "inspect it before using a new ID",
                )
            record = JobRecord(
                job_id=plan.job_id,
                request_sha256=plan.request_sha256,
                state=JobState.STAGING,
                remote_dir=plan.remote_dir,
            )
            self._save(record)
            transport = self._transport(request)
            try:
                if transport.path_exists(plan.remote_dir):
                    raise ToolkitError(
                        "REMOTE_JOB_EXISTS", "stage", f"remote directory exists: {plan.remote_dir}"
                    )
                with tempfile.TemporaryDirectory() as temp:
                    staging = Path(temp)
                    input_copy = staging / "input.in"
                    input_data = _read_file(request.job.input_file, "stage")
                    if _sha256(input_data) != plan.input_sha256:
                        raise ToolkitError(
                            "REQUEST_CHANGED", "stage", "QE input changed after preview"
                        )
                    input_copy.write_bytes(input_data)
                    pseudo_copies: list[tuple[Path, str]] = []
                    for path in request.job.pseudopotentials:
                        data = _read_file(path, "stage")
                        if _sha256(data) != plan.pseudopotential_sha256[path.name]:
                            raise ToolkitError(
                                "REQUEST_CHANGED", "stage", f"{path.name} changed after preview"
                            )
                        copy = staging / path.name
                        copy.write_bytes(data)
                        pseudo_copies.append((copy, path.name))
                    script = Path(temp) / "job.sbatch"
                    script.write_text(plan.script, encoding="utf-8")
                    transport.run(f"mkdir -p {shlex.quote(plan.remote_dir)}/pseudo", step="stage")
                    transport.upload(input_copy, f"{plan.remote_dir}/input.in")
                    for copy, name in pseudo_copies:
                        transport.upload(copy, f"{plan.remote_dir}/pseudo/{name}")
                    transport.upload(script, f"{plan.remote_dir}/job.sbatch")
            except ToolkitError as exc:
                record.state = JobState.STAGE_FAILED
                record.error = str(exc)
                self._save(record)
                raise
            except OSError as exc:
                record.state = JobState.STAGE_FAILED
                record.error = str(exc)
                self._save(record)
                raise ToolkitError("STAGE_IO_FAILED", "stage", str(exc)) from exc
            record.state = JobState.SUBMITTING
            self._save(record)
            try:
                output = transport.run(
                    f"cd {shlex.quote(plan.remote_dir)} && sbatch --parsable job.sbatch",
                    step="submit",
                ).strip()
                match = re.fullmatch(r"([0-9]+)(?:;[A-Za-z0-9_.-]+)?", output)
                if not match:
                    raise ToolkitError(
                        "SUBMISSION_UNCONFIRMED",
                        "submit",
                        f"unexpected sbatch response: {output!r}",
                    )
            except ToolkitError as exc:
                record.state = JobState.SUBMISSION_UNKNOWN
                record.error = str(exc)
                self._save(record)
                raise
            record.slurm_job_id = match.group(1)
            record.state = JobState.SUBMITTED
            self._save(record)
            return record

    def adopt(self, request: SubmissionRequest, slurm_job_id: str) -> JobRecord:
        """Record an independently verified Slurm ID after an ambiguous submission."""
        if not re.fullmatch(r"[0-9]+", slurm_job_id):
            raise ToolkitError("SCHEMA_INVALID", "adopt", "Slurm job ID must contain digits only")
        record = self._load(request.job.job_id)
        if record.state not in {JobState.SUBMISSION_UNKNOWN, JobState.SUBMITTING}:
            raise ToolkitError("STATE_INVALID", "adopt", f"current state: {record.state.value}")
        if self.preview(request).request_sha256 != record.request_sha256:
            raise ToolkitError("REQUEST_CHANGED", "adopt", "request changed since submission")
        record.slurm_job_id = slurm_job_id
        record.state = JobState.SUBMITTED
        record.error = None
        self._save(record)
        return record

    def status(self, request: SubmissionRequest) -> JobRecord:
        record = self._load(request.job.job_id)
        if record.state in _TERMINAL:
            return record
        if not record.slurm_job_id:
            raise ToolkitError(
                "SUBMISSION_UNCONFIRMED",
                "status",
                "no Slurm job ID; inspect cluster before retrying",
            )
        if self.preview(request).request_sha256 != record.request_sha256:
            raise ToolkitError(
                "REQUEST_CHANGED",
                "status",
                "request files or configuration changed after submission",
            )
        transport = self._transport(request)
        slurm_id = record.slurm_job_id
        queue = transport.run(f"squeue -h -j {slurm_id} -o %T", step="status").strip()
        if queue:
            raw_state = queue.splitlines()[0].strip()
        else:
            accounting = transport.run(
                f"sacct -X -P -n -j {slurm_id} -o State,ExitCode", step="status"
            ).strip()
            raw_state = (
                accounting.splitlines()[0].split("|", 1)[0].strip() if accounting else "UNKNOWN"
            )
        state_name = raw_state.split()[0].split("+")[0].upper() if raw_state else "UNKNOWN"
        mapped = {
            "PENDING": JobState.QUEUED,
            "CONFIGURING": JobState.QUEUED,
            "RUNNING": JobState.RUNNING,
            "COMPLETING": JobState.RUNNING,
            "COMPLETED": JobState.COMPLETED,
            "FAILED": JobState.FAILED,
            "CANCELLED": JobState.CANCELLED,
            "TIMEOUT": JobState.TIMEOUT,
            "OUT_OF_MEMORY": JobState.FAILED,
            "NODE_FAIL": JobState.FAILED,
            "PREEMPTED": JobState.FAILED,
        }.get(state_name, JobState.UNKNOWN)
        if mapped != JobState.UNKNOWN:
            record.state = mapped
            record.error = None
            self._save(record)
        return record

    def fetch(self, request: SubmissionRequest, destination: Path) -> QEParseResult:
        record = self._load(request.job.job_id)
        if record.state not in _TERMINAL:
            raise ToolkitError("JOB_NOT_TERMINAL", "fetch", f"current state: {record.state.value}")
        if self.preview(request).request_sha256 != record.request_sha256:
            raise ToolkitError(
                "REQUEST_CHANGED",
                "fetch",
                "request files or configuration changed after submission",
            )
        destination = Path(destination) / record.job_id
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolkitError("OUTPUT_IO_FAILED", "fetch", str(exc)) from exc
        transport = self._transport(request)
        for name in ("stdout.log", "stderr.log", "output.out"):
            remote = f"{record.remote_dir}/{name}"
            if transport.exists(remote):
                transport.download(remote, destination / name)
            elif name == "output.out" and record.state == JobState.COMPLETED:
                raise ToolkitError("OUTPUT_MISSING", "fetch", f"completed job has no {remote}")
        output_path = destination / "output.out"
        try:
            result = parse_qe_output(
                record.job_id, output_path.read_bytes() if output_path.exists() else None
            )
            (destination / "result.json").write_text(
                result.model_dump_json(indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise ToolkitError("OUTPUT_IO_FAILED", "fetch", str(exc)) from exc
        record.fetched_at = datetime.now(UTC)
        self._save(record)
        return result
