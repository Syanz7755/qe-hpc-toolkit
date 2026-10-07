"""OpenSSH transport. Tests supply an adapter with the same small interface."""

import subprocess
from pathlib import Path
from typing import Protocol

from .errors import ToolkitError


class Transport(Protocol):
    def run(self, command: str, *, step: str) -> str: ...
    def upload(self, local: Path, remote: str) -> None: ...
    def download(self, remote: str, local: Path) -> None: ...
    def exists(self, remote: str) -> bool: ...
    def path_exists(self, remote: str) -> bool: ...


class OpenSSHTransport:
    def __init__(
        self, host: str, connect_timeout_seconds: int = 15, operation_timeout_seconds: int = 600
    ) -> None:
        self.host = host
        self.timeout = connect_timeout_seconds
        self.operation_timeout = operation_timeout_seconds

    def _invoke(
        self, argv: list[str], *, step: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.operation_timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ToolkitError(
                "TRANSPORT_UNAVAILABLE", step, f"missing executable: {argv[0]}"
            ) from exc
        except OSError as exc:
            raise ToolkitError("TRANSPORT_UNAVAILABLE", step, str(exc), retryable=True) from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolkitError(
                "TRANSPORT_TIMEOUT", step, "SSH operation timed out", retryable=True
            ) from exc
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise ToolkitError("REMOTE_COMMAND_FAILED", step, detail, retryable=True)
        return result

    def _ssh_args(self) -> list[str]:
        return ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.timeout}", self.host]

    def _scp_args(self) -> list[str]:
        return ["scp", "-q", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.timeout}"]

    def run(self, command: str, *, step: str) -> str:
        return self._invoke([*self._ssh_args(), command], step=step).stdout

    def upload(self, local: Path, remote: str) -> None:
        self._invoke([*self._scp_args(), str(local), f"{self.host}:{remote}"], step="upload")

    def download(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        self._invoke([*self._scp_args(), f"{self.host}:{remote}", str(local)], step="download")

    def exists(self, remote: str) -> bool:
        # The caller validates remote paths before they reach this adapter.
        return self._probe(remote, "-f")

    def path_exists(self, remote: str) -> bool:
        return self._probe(remote, "-e")

    def _probe(self, remote: str, flag: str) -> bool:
        result = self._invoke(
            [*self._ssh_args(), f"test {flag} '{remote}'"], step="exists", check=False
        )
        if result.returncode not in (0, 1):
            raise ToolkitError(
                "REMOTE_COMMAND_FAILED",
                "exists",
                result.stderr.strip() or "SSH probe failed",
                retryable=True,
            )
        return result.returncode == 0
