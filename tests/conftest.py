"""Shared deterministic test fixtures."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest


@dataclass(frozen=True)
class FakeChdman:
    """One isolated fake-CHDMAN control and observation channel."""

    command: tuple[str, ...]
    control_path: Path
    record_path: Path
    environment: dict[str, str]

    def read_record(self) -> dict[str, Any]:
        """Read the latest complete observation record."""
        return json.loads(self.record_path.read_text(encoding="utf-8"))

    def wait_until_running(self, timeout: float = 5.0) -> dict[str, Any]:
        """Wait for the child to publish its running state."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.record_path.exists():
                record = self.read_record()
                if record.get("state") == "running":
                    return record
            time.sleep(0.02)
        raise AssertionError("fake CHDMAN did not reach the running state")


@pytest.fixture
def fake_chdman_factory(
    tmp_path: Path,
) -> Callable[[dict[str, Any]], FakeChdman]:
    """Create fake invocations without relying on shell command construction."""
    sequence = 0
    fake_script = Path(__file__).with_name("fake_chdman.py").resolve()

    def create(control: dict[str, Any]) -> FakeChdman:
        nonlocal sequence
        sequence += 1
        control_path = tmp_path / f"fake-control-{sequence}.json"
        record_path = tmp_path / f"fake-record-{sequence}.json"
        control_path.write_text(
            json.dumps(control, ensure_ascii=False),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            {
                "FAKE_CHDMAN_CONTROL": str(control_path),
                "FAKE_CHDMAN_RECORD": str(record_path),
            }
        )
        return FakeChdman(
            command=(sys.executable, str(fake_script)),
            control_path=control_path,
            record_path=record_path,
            environment=environment,
        )

    return create
