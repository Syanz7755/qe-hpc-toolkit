from pathlib import Path

import pytest
from pydantic import ValidationError

from qe_hpc_toolkit.cli import _load_request
from qe_hpc_toolkit.errors import ToolkitError
from qe_hpc_toolkit.models import ClusterConfig, JobSpec, JobState, Resources, SubmissionRequest
from qe_hpc_toolkit.toolkit import QEHpcToolkit

QE_INPUT = """&CONTROL
 calculation = 'scf'
 prefix = 'mgo'
 pseudo_dir = './pseudo'
 outdir = './outdir'
/
&SYSTEM
 ibrav = 1
 celldm(1) = 8.0
 nat = 2
 ntyp = 2
 ecutwfc = 50
/
&ELECTRONS
 conv_thr = 1.0d-8
/
ATOMIC_SPECIES
Mg 24.305 Mg.upf
O 15.999 O.upf
ATOMIC_POSITIONS angstrom
Mg 0 0 0
O 2 2 2
K_POINTS gamma
"""


class FakeTransport:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.queue = "PENDING\n"
        self.accounting = "COMPLETED|0:0\n"
        self.submit_error = False

    def run(self, command: str, *, step: str) -> str:
        self.commands.append(command)
        if step == "submit":
            if self.submit_error:
                raise ToolkitError("TRANSPORT_TIMEOUT", "submit", "network timeout", retryable=True)
            return "12345\n"
        if command.startswith("squeue"):
            return self.queue
        if command.startswith("sacct"):
            return self.accounting
        return ""

    def upload(self, local: Path, remote: str) -> None:
        self.files[remote] = local.read_bytes()

    def download(self, remote: str, local: Path) -> None:
        local.write_bytes(self.files[remote])

    def exists(self, remote: str) -> bool:
        return remote in self.files

    def path_exists(self, remote: str) -> bool:
        return any(item.startswith(remote + "/") for item in self.files)


@pytest.fixture
def submission(tmp_path: Path) -> SubmissionRequest:
    input_file = tmp_path / "mgo.in"
    input_file.write_text(QE_INPUT, encoding="utf-8")
    mg = tmp_path / "Mg.upf"
    oxygen = tmp_path / "O.upf"
    mg.write_text("Mg pseudo", encoding="utf-8")
    oxygen.write_text("O pseudo", encoding="utf-8")
    return SubmissionRequest(
        cluster=ClusterConfig(host="test-cluster", remote_root="/scratch/qe", modules=["qe/7.2"]),
        job=JobSpec(
            job_id="mgo_scf_01",
            calculation="scf",
            input_file=input_file,
            pseudopotentials=[mg, oxygen],
            resources=Resources(
                partition="compute",
                mpi_ranks=4,
                threads_per_rank=2,
                memory="16G",
                walltime="02:00:00",
            ),
        ),
    )


def test_preview_validates_files_without_transport(
    submission: SubmissionRequest, tmp_path: Path
) -> None:
    toolkit = QEHpcToolkit(tmp_path / "state")
    plan = toolkit.preview(submission)
    assert plan.input_sha256
    assert "#SBATCH --ntasks=4" in plan.script
    assert "module load qe/7.2" in plan.script
    assert not (tmp_path / "state").exists()
    submission.job.input_file.write_text(QE_INPUT.replace("Mg.upf", "Other.upf"), encoding="utf-8")
    with pytest.raises(ToolkitError, match="PSEUDO_MISSING"):
        toolkit.preview(submission)


def test_preview_rejects_calculation_mismatch(
    submission: SubmissionRequest, tmp_path: Path
) -> None:
    toolkit = QEHpcToolkit(tmp_path / "state")
    changed = submission.model_copy(
        update={"job": submission.job.model_copy(update={"calculation": "relax"})}
    )
    with pytest.raises(ToolkitError, match="calculation must match"):
        toolkit.preview(changed)


def test_strict_schema_rejects_unknown_field(submission: SubmissionRequest) -> None:
    payload = submission.model_dump(mode="json")
    payload["job"]["surprise"] = "ignored?"
    with pytest.raises(ValidationError):
        SubmissionRequest.model_validate(payload)


def test_cli_resolves_paths_relative_to_json(submission: SubmissionRequest, tmp_path: Path) -> None:
    folder = tmp_path / "case"
    folder.mkdir()
    config = folder / "request.json"
    payload = submission.model_dump(mode="json")
    payload["job"]["input_file"] = "../mgo.in"
    payload["job"]["pseudopotentials"] = ["../Mg.upf", "../O.upf"]
    config.write_text(__import__("json").dumps(payload), encoding="utf-8")
    loaded = _load_request(config)
    assert loaded.job.input_file == submission.job.input_file
    assert loaded.job.pseudopotentials == submission.job.pseudopotentials


def test_submit_status_fetch_and_duplicate_guard(
    submission: SubmissionRequest, tmp_path: Path
) -> None:
    fake = FakeTransport()
    toolkit = QEHpcToolkit(tmp_path / "state", fake)
    record = toolkit.submit(submission)
    assert record.slurm_job_id == "12345"
    assert record.state == JobState.SUBMITTED
    assert "/scratch/qe/mgo_scf_01/pseudo/Mg.upf" in fake.files
    assert "sbatch --parsable job.sbatch" in fake.commands[-1]
    first_command_count = len(fake.commands)
    with pytest.raises(ToolkitError, match="JOB_EXISTS"):
        toolkit.submit(submission)
    assert len(fake.commands) == first_command_count
    assert toolkit.status(submission).state == JobState.QUEUED
    fake.queue = ""
    assert toolkit.status(submission).state == JobState.COMPLETED
    fake.files["/scratch/qe/mgo_scf_01/output.out"] = (
        b"convergence has been achieved\n! total energy = -75.123 Ry\nJOB DONE.\n"
    )
    result = toolkit.fetch(submission, tmp_path / "artifacts")
    assert result.job_done and result.convergence_achieved
    assert result.total_energy_ry == pytest.approx(-75.123)
    assert (tmp_path / "artifacts" / "mgo_scf_01" / "result.json").exists()
    assert toolkit._load("mgo_scf_01").fetched_at is not None


def test_ambiguous_submit_is_persisted_and_never_retried(
    submission: SubmissionRequest, tmp_path: Path
) -> None:
    fake = FakeTransport()
    fake.submit_error = True
    toolkit = QEHpcToolkit(tmp_path / "state", fake)
    with pytest.raises(ToolkitError, match="TRANSPORT_TIMEOUT"):
        toolkit.submit(submission)
    assert toolkit._load("mgo_scf_01").state == JobState.SUBMISSION_UNKNOWN
    with pytest.raises(ToolkitError, match="JOB_EXISTS"):
        toolkit.submit(submission)
    with pytest.raises(ToolkitError, match="SUBMISSION_UNCONFIRMED"):
        toolkit.status(submission)
    assert toolkit.adopt(submission, "98765").slurm_job_id == "98765"
    assert toolkit.status(submission).state == JobState.QUEUED


def test_remote_directory_collision_stops_before_upload(
    submission: SubmissionRequest, tmp_path: Path
) -> None:
    fake = FakeTransport()
    fake.files["/scratch/qe/mgo_scf_01/unrelated"] = b"keep"
    toolkit = QEHpcToolkit(tmp_path / "state", fake)
    with pytest.raises(ToolkitError, match="REMOTE_JOB_EXISTS"):
        toolkit.submit(submission)
    assert toolkit._load("mgo_scf_01").state == JobState.STAGE_FAILED
    assert fake.files == {"/scratch/qe/mgo_scf_01/unrelated": b"keep"}


def test_accounting_lag_preserves_last_known_state(
    submission: SubmissionRequest, tmp_path: Path
) -> None:
    fake = FakeTransport()
    toolkit = QEHpcToolkit(tmp_path / "state", fake)
    toolkit.submit(submission)
    assert toolkit.status(submission).state == JobState.QUEUED
    fake.queue = ""
    fake.accounting = ""
    assert toolkit.status(submission).state == JobState.QUEUED
