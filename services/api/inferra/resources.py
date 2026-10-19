"""What this container may use. CPU and memory come from its cgroup limits, so DuckDB and LightGBM
size themselves to a 0.1-CPU, 512 MB instance instead of to the much larger host it runs on."""

import os

from inferra.config import get_settings

MEMORY_FILES = ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes")
DUCKDB_SHARE = 0.35  # the rest is the Python process: the API, pandas frames, and model fitting
DUCKDB_FLOOR_MB = 128


def cpu_threads(most: int = 2, cpu_max: str = "/sys/fs/cgroup/cpu.max") -> int:
    """Threads that do not oversubscribe a CPU-limited container (a 0.1-CPU free instance runs far
    slower with two spinning threads than with one)."""
    cpus = float(len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 1)
    try:
        with open(cpu_max) as fh:
            quota, period = fh.read().split()[:2]
        if quota != "max":
            cpus = min(cpus, int(quota) / int(period))
    except (OSError, ValueError):
        pass
    return max(1, min(most, int(cpus)))


def memory_limit_bytes(paths: tuple[str, ...] = MEMORY_FILES) -> int | None:
    """The container's memory limit (cgroup v2, then v1), or None when there is none."""
    for path in paths:
        try:
            with open(path) as fh:
                raw = fh.read().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        return None if value >= 1 << 60 else value  # v1 reports "unlimited" as a huge number
    return None


def duckdb_memory_mb() -> int | None:
    """DuckDB's memory cap. Without one it sizes itself to the host's RAM, and a large join can take
    the whole container down instead of failing as one query."""
    configured = get_settings().duckdb_memory_mb
    if configured:
        return configured
    limit = memory_limit_bytes()
    return max(DUCKDB_FLOOR_MB, int(limit * DUCKDB_SHARE) >> 20) if limit else None


def limit_duckdb(con: object) -> None:
    """Apply the thread and memory caps to a new DuckDB connection, before its config is locked."""
    con.execute(f"SET threads={cpu_threads(most=4)}")  # type: ignore[attr-defined]
    mb = duckdb_memory_mb()
    if mb:
        con.execute(f"SET memory_limit='{mb}MB'")  # type: ignore[attr-defined]
