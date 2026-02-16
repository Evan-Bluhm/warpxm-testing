"""Run WARPXM benchmarks and collect timing data."""

import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import database as db
from . import hardware, timing_parser


def run_benchmark(
    benchmark_name: str,
    input_file: Path,
    warpxm_exec: Path,
    build_id: int,
    conn,
    num_procs: int = 0,
    mpirun: str = "mpiexec",
    work_dir: Path | None = None,
) -> dict:
    """Run a single benchmark and store results.

    Args:
        benchmark_name: Name of this benchmark (e.g. "advection").
        input_file: Path to the WARPXM .inp XML input file.
        warpxm_exec: Path to the warpxm binary.
        build_id: Database ID of the build used.
        conn: Database connection.
        num_procs: Number of MPI processes (0 = serial).
        mpirun: MPI launcher command.
        work_dir: Working directory for the run.

    Returns:
        Dict with run_id, wall_time_s, success, timing data.
    """
    hw = hardware.get_hardware_info()

    run_id = db.insert_run(
        conn,
        build_id=build_id,
        benchmark_name=benchmark_name,
        hardware_id=hw["hardware_id"],
        cpu=hw["cpu"],
        gpu=hw["gpu"],
        num_procs=max(num_procs, 1),
    )

    # Copy the .inp file into the work directory so all WARPXM output
    # (data/, log/, meshes/) is generated there instead of next to the original.
    cwd = work_dir or input_file.parent
    cwd.mkdir(parents=True, exist_ok=True)
    local_input = cwd / input_file.name
    if local_input != input_file:
        shutil.copy2(input_file, local_input)

    # Build command (warpxm uses -i flag for input file)
    if num_procs > 0:
        cmd = [mpirun, "-np", str(num_procs), str(warpxm_exec), "-i", str(local_input)]
    else:
        cmd = [str(warpxm_exec), "-i", str(local_input)]

    env = {
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    print(f"[run] {' '.join(cmd)} (cwd={cwd})")

    run_env = {**os.environ, **env}

    wall_start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env=run_env,
            capture_output=True,
            text=True,
        )
        wall_time_s = time.monotonic() - wall_start
        success = result.returncode == 0
    except Exception as e:
        wall_time_s = time.monotonic() - wall_start
        success = False
        print(f"[run] Error: {e}", file=sys.stderr)
        result = None

    # Parse timing from stdout
    output = result.stdout if result else ""
    timing = timing_parser.parse_timing_report(output)

    if not success and result:
        print(f"[run] FAILED (exit code {result.returncode})", file=sys.stderr)
        if result.stderr:
            # Print last 20 lines of stderr
            lines = result.stderr.strip().splitlines()
            for line in lines[-20:]:
                print(f"  {line}", file=sys.stderr)

    # Store results
    db.finish_run(conn, run_id, wall_time_s, success)

    if timing["scopes"]:
        db.insert_timing_scopes(conn, run_id, timing["scopes"])

    # Clean up WARPXM output files
    _cleanup_run_artifacts(cwd)

    return {
        "run_id": run_id,
        "wall_time_s": wall_time_s,
        "success": success,
        "timing": timing,
        "stdout": output,
        "stderr": result.stderr if result else "",
    }


def run_benchmark_averaged(
    benchmark_name: str,
    input_file: Path,
    warpxm_exec: Path,
    build_id: int,
    conn,
    num_runs: int = 3,
    num_procs: int = 0,
    mpirun: str = "mpiexec",
    work_dir: Path | None = None,
    git_sha: str = "",
) -> dict:
    """Run a benchmark multiple times and compute averages.

    Returns:
        Dict with aggregate_id, individual results, and summary statistics.
    """
    hw = hardware.get_hardware_info()
    results = []

    for i in range(num_runs):
        print(f"\n--- Run {i + 1}/{num_runs} for '{benchmark_name}' ---")
        r = run_benchmark(
            benchmark_name=benchmark_name,
            input_file=input_file,
            warpxm_exec=warpxm_exec,
            build_id=build_id,
            conn=conn,
            num_procs=num_procs,
            mpirun=mpirun,
            work_dir=work_dir,
        )
        results.append(r)
        if r["success"]:
            print(f"  Wall time: {r['wall_time_s']:.3f}s")
        else:
            print("  FAILED")

    # Compute averages over successful runs
    successful = [r for r in results if r["success"]]
    if not successful:
        print("All runs failed, no aggregate computed.")
        return {"results": results, "aggregate_id": None}

    wall_times = [r["wall_time_s"] for r in successful]
    mean_wall = sum(wall_times) / len(wall_times)
    stddev_wall = (
        math.sqrt(sum((t - mean_wall) ** 2 for t in wall_times) / len(wall_times))
        if len(wall_times) > 1
        else None
    )

    # Aggregate scope timings across successful runs
    all_scope_names = set()
    for r in successful:
        for s in r["timing"]["scopes"]:
            all_scope_names.add(s["scope"])

    scope_stats = []
    for scope_name in sorted(all_scope_names):
        values = []
        for r in successful:
            for s in r["timing"]["scopes"]:
                if s["scope"] == scope_name:
                    values.append(s["elapsed_ms"])
                    break
        if values:
            mean_ms = sum(values) / len(values)
            stddev_ms = (
                math.sqrt(sum((v - mean_ms) ** 2 for v in values) / len(values))
                if len(values) > 1
                else None
            )
            scope_stats.append(
                {
                    "scope": scope_name,
                    "mean_elapsed_ms": mean_ms,
                    "stddev_elapsed_ms": stddev_ms,
                }
            )

    agg_id = db.insert_aggregate(
        conn,
        benchmark_name=benchmark_name,
        hardware_id=hw["hardware_id"],
        git_sha=git_sha,
        num_runs=len(successful),
        mean_wall_time_s=mean_wall,
        stddev_wall_time_s=stddev_wall,
        scope_stats=scope_stats,
    )

    print(f"\n=== Aggregate for '{benchmark_name}' ===")
    print(f"  Successful runs: {len(successful)}/{num_runs}")
    print(f"  Mean wall time:  {mean_wall:.3f}s")
    if stddev_wall is not None:
        print(f"  Std dev:         {stddev_wall:.3f}s")

    return {
        "results": results,
        "aggregate_id": agg_id,
        "mean_wall_time_s": mean_wall,
        "stddev_wall_time_s": stddev_wall,
        "scope_stats": scope_stats,
    }


def _cleanup_run_artifacts(work_dir: Path) -> None:
    """Remove WARPXM output files from the working directory."""
    # Directories WARPXM creates
    for dirname in ("meshes", "data", "log"):
        d = work_dir / dirname
        if d.exists():
            shutil.rmtree(d)

    # Generated mesh input files and copied benchmark .inp
    for f in work_dir.glob("*.inp"):
        f.unlink()

    # Stray .h5 files in the work dir
    for f in work_dir.glob("*.h5"):
        f.unlink()
