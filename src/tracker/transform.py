"""Run the dbt project (silver/gold models + data tests) in-process."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from tracker.log import get_logger
from tracker.settings import get_settings

log = get_logger(__name__)


@contextmanager
def _env(name: str, value: str) -> Iterator[None]:
    """Set an environment variable for the duration of the block (dbt reads it via env_var)."""
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            del os.environ[name]
        else:
            os.environ[name] = previous


@dataclass
class DbtOutcome:
    """Summary of a dbt invocation."""

    success: bool
    counts: dict[str, int] = field(default_factory=dict)  # status -> n nodes
    failures: list[str] = field(default_factory=list)  # names of failed models/tests


def run_dbt(
    command: str = "build", db_path: Path | None = None, select: str | None = None
) -> DbtOutcome:
    """Run `dbt <command>` against the tracker database.

    dbt opens the DuckDB file itself, so callers must not hold a write
    connection while this runs.
    """
    from dbt.cli.main import dbtRunner  # imported lazily: dbt is slow to import

    settings = get_settings()
    project_dir = settings.dbt_project_dir.resolve()
    args = [command, "--project-dir", str(project_dir), "--profiles-dir", str(project_dir)]
    if select:
        args += ["--select", select]
    with _env("TRACKER_DB_PATH", str((db_path or settings.db_path).resolve())):
        result = dbtRunner().invoke(args)

    outcome = DbtOutcome(success=bool(result.success))
    for node in getattr(result.result, "results", None) or []:
        status = str(node.status)
        outcome.counts[status] = outcome.counts.get(status, 0) + 1
        if status in {"error", "fail"}:
            outcome.failures.append(node.node.name)
    if result.exception is not None:
        outcome.failures.append(f"dbt crashed: {result.exception}")
    log.info("dbt_finished", command=command, success=outcome.success, counts=outcome.counts)
    if outcome.failures:
        log.error("dbt_failures", failures=outcome.failures)
    return outcome
