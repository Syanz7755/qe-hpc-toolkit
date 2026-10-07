"""Stable machine-readable failures at the toolkit interface."""

from dataclasses import dataclass
from typing import Any


@dataclass(eq=False)
class ToolkitError(Exception):
    code: str
    step: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.code} at {self.step}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "step": self.step,
            "message": self.message,
            "retryable": self.retryable,
        }
