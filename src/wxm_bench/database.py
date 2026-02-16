"""SQLite database for storing benchmark timing results."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "benchmarks.db"


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Create tables if they don't exist."""
    conn = get_connection(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS builds (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            git_sha     TEXT NOT NULL,
            git_branch  TEXT,
            build_type  TEXT NOT NULL DEFAULT 'Release',
            cmake_args  TEXT,
            built_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS benchmark_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            build_id        INTEGER NOT NULL REFERENCES builds(id),
            benchmark_name  TEXT NOT NULL,
            hardware_id     TEXT NOT NULL,
            cpu             TEXT NOT NULL,
            gpu             TEXT NOT NULL,
            num_procs       INTEGER NOT NULL DEFAULT 1,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            wall_time_s     REAL,
            success         INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS timing_scopes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL REFERENCES benchmark_runs(id),
            scope           TEXT NOT NULL,
            elapsed_ms      REAL NOT NULL,
            percent_total   REAL
        );

        CREATE TABLE IF NOT EXISTS aggregate_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            benchmark_name  TEXT NOT NULL,
            hardware_id     TEXT NOT NULL,
            git_sha         TEXT NOT NULL,
            num_procs       INTEGER NOT NULL DEFAULT 1,
            num_runs        INTEGER NOT NULL,
            mean_wall_time_s    REAL NOT NULL,
            stddev_wall_time_s  REAL,
            computed_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS aggregate_scopes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            aggregate_id    INTEGER NOT NULL REFERENCES aggregate_results(id),
            scope           TEXT NOT NULL,
            mean_elapsed_ms REAL NOT NULL,
            stddev_elapsed_ms REAL
        );
        """
    )
    conn.commit()

    # Migrations for existing databases
    _migrate_add_column(
        conn, "aggregate_results", "num_procs", "INTEGER NOT NULL DEFAULT 1"
    )

    conn.close()


def _migrate_add_column(
    conn: sqlite3.Connection, table: str, column: str, column_def: str
) -> None:
    """Add a column to a table if it doesn't already exist."""
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
        conn.commit()


def insert_build(
    conn: sqlite3.Connection,
    git_sha: str,
    git_branch: str | None,
    build_type: str,
    cmake_args: str | None,
) -> int:
    cur = conn.execute(
        """INSERT INTO builds (git_sha, git_branch, build_type, cmake_args, built_at)
           VALUES (?, ?, ?, ?, ?)""",
        (git_sha, git_branch, build_type, cmake_args, _now()),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def find_build(conn: sqlite3.Connection, git_sha: str, build_type: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM builds WHERE git_sha = ? AND build_type = ? ORDER BY id DESC LIMIT 1",
        (git_sha, build_type),
    ).fetchone()
    return dict(row) if row else None


def insert_run(
    conn: sqlite3.Connection,
    build_id: int,
    benchmark_name: str,
    hardware_id: str,
    cpu: str,
    gpu: str,
    num_procs: int = 1,
) -> int:
    cur = conn.execute(
        """INSERT INTO benchmark_runs
           (build_id, benchmark_name, hardware_id, cpu, gpu, num_procs, started_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (build_id, benchmark_name, hardware_id, cpu, gpu, num_procs, _now()),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    wall_time_s: float,
    success: bool,
) -> None:
    conn.execute(
        """UPDATE benchmark_runs
           SET finished_at = ?, wall_time_s = ?, success = ?
           WHERE id = ?""",
        (_now(), wall_time_s, int(success), run_id),
    )
    conn.commit()


def insert_timing_scopes(
    conn: sqlite3.Connection,
    run_id: int,
    scopes: list[dict],
) -> None:
    conn.executemany(
        """INSERT INTO timing_scopes (run_id, scope, elapsed_ms, percent_total)
           VALUES (?, ?, ?, ?)""",
        [(run_id, s["scope"], s["elapsed_ms"], s.get("percent_total")) for s in scopes],
    )
    conn.commit()


def insert_aggregate(
    conn: sqlite3.Connection,
    benchmark_name: str,
    hardware_id: str,
    git_sha: str,
    num_procs: int,
    num_runs: int,
    mean_wall_time_s: float,
    stddev_wall_time_s: float | None,
    scope_stats: list[dict],
) -> int:
    cur = conn.execute(
        """INSERT INTO aggregate_results
           (benchmark_name, hardware_id, git_sha, num_procs, num_runs,
            mean_wall_time_s, stddev_wall_time_s, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            benchmark_name,
            hardware_id,
            git_sha,
            num_procs,
            num_runs,
            mean_wall_time_s,
            stddev_wall_time_s,
            _now(),
        ),
    )
    assert cur.lastrowid is not None
    agg_id = cur.lastrowid
    conn.executemany(
        """INSERT INTO aggregate_scopes
           (aggregate_id, scope, mean_elapsed_ms, stddev_elapsed_ms)
           VALUES (?, ?, ?, ?)""",
        [
            (agg_id, s["scope"], s["mean_elapsed_ms"], s.get("stddev_elapsed_ms"))
            for s in scope_stats
        ],
    )
    conn.commit()
    return agg_id


def get_runs_for_aggregate(
    conn: sqlite3.Connection,
    benchmark_name: str,
    hardware_id: str,
    git_sha: str,
) -> list[dict]:
    """Get all successful runs for a given benchmark/hardware/sha combination."""
    rows = conn.execute(
        """SELECT br.*, b.git_sha
           FROM benchmark_runs br
           JOIN builds b ON br.build_id = b.id
           WHERE br.benchmark_name = ?
             AND br.hardware_id = ?
             AND b.git_sha = ?
             AND br.success = 1
           ORDER BY br.id""",
        (benchmark_name, hardware_id, git_sha),
    ).fetchall()
    return [dict(r) for r in rows]


def get_scopes_for_run(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM timing_scopes WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_aggregates(
    conn: sqlite3.Connection,
    benchmark_name: str | None = None,
    hardware_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    query = "SELECT * FROM aggregate_results WHERE 1=1"
    params: list = []
    if benchmark_name:
        query += " AND benchmark_name = ?"
        params.append(benchmark_name)
    if hardware_id:
        query += " AND hardware_id = ?"
        params.append(hardware_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
