"""Versioned request and result schemas. Unknown fields are rejected."""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Resources(StrictModel):
    partition: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    account: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$")
    mpi_ranks: int = Field(ge=1, le=4096)
    threads_per_rank: int = Field(ge=1, le=256)
    memory: str = Field(pattern=r"^[1-9][0-9]*[MGT]$")
    walltime: str = Field(pattern=r"^(?:[0-9]+-)?[0-9]{2}:[0-5][0-9]:[0-5][0-9]$")


class ClusterConfig(StrictModel):
    host: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    remote_root: str
    qe_executable: str = Field(default="pw.x", pattern=r"^[A-Za-z0-9_./+-]+$")
    mpi_launcher: Literal["srun", "mpirun"] = "srun"
    modules: list[str] = Field(default_factory=list)
    connect_timeout_seconds: int = Field(default=15, ge=1, le=120)
    operation_timeout_seconds: int = Field(default=600, ge=30, le=86400)

    @field_validator("remote_root")
    @classmethod
    def validate_remote_root(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in value.split("/"):
            raise ValueError("remote_root must be an absolute POSIX path with safe segments")
        return value.rstrip("/") or "/"

    @field_validator("modules")
    @classmethod
    def validate_modules(cls, values: list[str]) -> list[str]:
        import re

        if any(not re.fullmatch(r"[A-Za-z0-9_.+/-]+", item) for item in values):
            raise ValueError("module names may only contain letters, digits, _, ., +, /, -")
        return values


class JobSpec(StrictModel):
    job_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    calculation: Literal["scf", "relax", "vc-relax", "nscf", "bands"]
    input_file: Path
    pseudopotentials: list[Path] = Field(min_length=1)
    resources: Resources

    @model_validator(mode="after")
    def unique_pseudopotential_names(self) -> "JobSpec":
        names = [path.name for path in self.pseudopotentials]
        if len(set(names)) != len(names):
            raise ValueError("pseudopotential basenames must be unique")
        return self


class SubmissionRequest(StrictModel):
    schema_version: Literal[1] = 1
    cluster: ClusterConfig
    job: JobSpec


class SubmissionPlan(StrictModel):
    schema_version: Literal[1] = 1
    job_id: str
    request_sha256: str
    remote_dir: str
    input_sha256: str
    pseudopotential_sha256: dict[str, str]
    script: str


class JobState(StrEnum):
    STAGING = "staging"
    STAGE_FAILED = "stage_failed"
    SUBMITTING = "submitting"
    SUBMISSION_UNKNOWN = "submission_unknown"
    SUBMITTED = "submitted"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class JobRecord(StrictModel):
    schema_version: Literal[1] = 1
    job_id: str
    request_sha256: str
    state: JobState
    remote_dir: str
    slurm_job_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fetched_at: datetime | None = None
    error: str | None = None


class QEParseResult(StrictModel):
    schema_version: Literal[1] = 1
    job_id: str
    output_sha256: str | None = None
    job_done: bool = False
    convergence_achieved: bool = False
    total_energy_ry: float | None = None
    total_energy_ev: float | None = None
    warnings: list[str] = Field(default_factory=list)
