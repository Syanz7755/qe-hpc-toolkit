"""Small JSON CLI for preview, submission, status, and collection."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .errors import ToolkitError
from .models import JobRecord, QEParseResult, SubmissionPlan, SubmissionRequest
from .toolkit import QEHpcToolkit


def _load_request(path: Path) -> SubmissionRequest:
    try:
        request = SubmissionRequest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolkitError("CONFIG_UNAVAILABLE", "config", str(exc)) from exc
    base = path.resolve().parent
    job = request.job
    input_file = job.input_file if job.input_file.is_absolute() else base / job.input_file
    pseudos = [item if item.is_absolute() else base / item for item in job.pseudopotentials]
    return request.model_copy(
        update={
            "job": job.model_copy(
                update={
                    "input_file": input_file.resolve(),
                    "pseudopotentials": [item.resolve() for item in pseudos],
                }
            )
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qe-hpc")
    parser.add_argument(
        "command", choices=["schema", "preview", "submit", "adopt", "status", "fetch"]
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state-dir", type=Path, default=Path(".qe-hpc"))
    parser.add_argument("--destination", type=Path, default=Path("artifacts"))
    parser.add_argument("--slurm-id")
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps(SubmissionRequest.model_json_schema(), indent=2))
        return 0
    if args.config is None:
        parser.error("--config is required for this command")
    try:
        request = _load_request(args.config)
        toolkit = QEHpcToolkit(args.state_dir)
        result: SubmissionPlan | JobRecord | QEParseResult
        if args.command == "preview":
            result = toolkit.preview(request)
        elif args.command == "submit":
            result = toolkit.submit(request)
        elif args.command == "adopt":
            if args.slurm_id is None:
                parser.error("--slurm-id is required for adopt")
            result = toolkit.adopt(request, args.slurm_id)
        elif args.command == "status":
            result = toolkit.status(request)
        else:
            result = toolkit.fetch(request, args.destination)
        print(result.model_dump_json(indent=2))
        return 0
    except (ToolkitError, ValidationError) as exc:
        error = (
            exc.as_dict()
            if isinstance(exc, ToolkitError)
            else {
                "code": "SCHEMA_INVALID",
                "step": "config",
                "message": str(exc),
                "retryable": False,
            }
        )
        print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
