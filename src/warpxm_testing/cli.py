"""Command-line interface for warpxm-test."""

import argparse
from pathlib import Path

from . import benchmarks, builder, hardware, runner
from . import database as db

DEFAULT_SOURCE_DIR = Path.home() / "GitHub" / "warpxm"
DEFAULT_BUILD_DIR = Path.home() / "GitHub" / "warpxm" / "build"


def cmd_hw_info(args):
    """Print detected hardware information."""
    info = hardware.get_hardware_info()
    print(f"CPU:         {info['cpu']}")
    print(f"GPU:         {info['gpu']}")
    print(f"Hardware ID: {info['hardware_id']}")


def cmd_init_db(args):
    """Initialize the database."""
    db_path = Path(args.db)
    db.init_db(db_path)
    print(f"Database initialized at {db_path}")


def cmd_build(args):
    """Build WARPXM."""
    source_dir = Path(args.source_dir)
    build_dir = Path(args.build_dir)

    extra_args = args.cmake_args.split() if args.cmake_args else None

    print(f"Building WARPXM from {source_dir}")
    print(f"Build directory: {build_dir}")
    print(f"Build type: {args.build_type}")

    info = builder.build_warpxm(
        source_dir=source_dir,
        build_dir=build_dir,
        build_type=args.build_type,
        extra_cmake_args=extra_args,
        jobs=args.jobs,
    )

    # Store build in DB
    db.init_db(Path(args.db))
    conn = db.get_connection(Path(args.db))
    build_id = db.insert_build(
        conn,
        git_sha=info["git_sha"],
        git_branch=info["git_branch"],
        build_type=info["build_type"],
        cmake_args=info["cmake_args"],
    )
    conn.close()

    print(f"\nBuild successful (build_id={build_id})")
    print(f"  Git SHA:    {info['git_sha'][:12]}")
    print(f"  Branch:     {info['git_branch']}")
    print(f"  Executable: {info['warpxm_exec']}")


def cmd_run(args):
    """Run a benchmark."""
    db_path = Path(args.db)
    db.init_db(db_path)
    conn = db.get_connection(db_path)

    source_dir = Path(args.source_dir)
    build_dir = Path(args.build_dir)

    # Resolve build
    warpxm_exec = builder.get_warpxm_exec(build_dir)
    git_info = builder.get_git_info(source_dir)

    # Find or create a build record
    existing = db.find_build(conn, git_info["sha"], args.build_type)
    if existing:
        build_id = existing["id"]
        print(f"Using existing build record (build_id={build_id})")
    else:
        build_id = db.insert_build(
            conn,
            git_sha=git_info["sha"],
            git_branch=git_info["branch"],
            build_type=args.build_type,
            cmake_args=None,
        )
        print(f"Created build record (build_id={build_id})")

    # Resolve the input file
    benchmark_name = args.benchmark
    input_file = benchmarks.get_input_file(benchmark_name)

    work_dir = Path(args.work_dir) if args.work_dir else Path.cwd() / "benchmark_runs"
    benchmark_work_dir = work_dir / benchmark_name
    benchmark_work_dir.mkdir(parents=True, exist_ok=True)

    # Run it
    result = runner.run_benchmark_averaged(
        benchmark_name=benchmark_name,
        input_file=input_file,
        warpxm_exec=warpxm_exec,
        build_id=build_id,
        conn=conn,
        num_runs=args.num_runs,
        num_procs=args.num_procs,
        work_dir=benchmark_work_dir,
        git_sha=git_info["sha"],
    )

    conn.close()
    return result


def cmd_list(args):
    """List available benchmarks."""
    names = benchmarks.list_benchmarks()
    if not names:
        print("No benchmark .inp files found.")
        return
    for name in names:
        print(name)


def cmd_results(args):
    """Show stored benchmark results."""
    db_path = Path(args.db)
    db.init_db(db_path)
    conn = db.get_connection(db_path)

    aggregates = db.get_latest_aggregates(
        conn,
        benchmark_name=args.benchmark,
        hardware_id=args.hardware_id,
        limit=args.limit,
    )

    if not aggregates:
        print("No results found.")
        conn.close()
        return

    for agg in aggregates:
        print(f"\n{'=' * 60}")
        print(f"Benchmark:   {agg['benchmark_name']}")
        print(f"Hardware:    {agg['hardware_id']}")
        print(f"Git SHA:     {agg['git_sha'][:12]}")
        print(f"Runs:        {agg['num_runs']}")
        print(f"Mean wall:   {agg['mean_wall_time_s']:.3f}s")
        if agg["stddev_wall_time_s"] is not None:
            print(f"Std dev:     {agg['stddev_wall_time_s']:.3f}s")
        print(f"Computed at: {agg['computed_at']}")

        # Show scope timings
        scope_rows = conn.execute(
            "SELECT * FROM aggregate_scopes WHERE aggregate_id = ? ORDER BY mean_elapsed_ms DESC",
            (agg["id"],),
        ).fetchall()
        if scope_rows:
            print(f"\n  {'Scope':<50} {'Mean (ms)':>12} {'Stddev (ms)':>12}")
            print(f"  {'-' * 50} {'-' * 12} {'-' * 12}")
            for s in scope_rows:
                stddev = (
                    f"{s['stddev_elapsed_ms']:.2f}" if s["stddev_elapsed_ms"] else "n/a"
                )
                print(f"  {s['scope']:<50} {s['mean_elapsed_ms']:>12.2f} {stddev:>12}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        prog="warpxm-test",
        description="Performance testing framework for WARPXM",
    )
    parser.add_argument(
        "--db",
        default=str(db.DEFAULT_DB_PATH),
        help="Path to SQLite database file",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # hw-info
    sub = subparsers.add_parser("hw-info", help="Show detected hardware")
    sub.set_defaults(func=cmd_hw_info)

    # init-db
    sub = subparsers.add_parser("init-db", help="Initialize the database")
    sub.set_defaults(func=cmd_init_db)

    # list
    sub = subparsers.add_parser("list", help="List available benchmarks")
    sub.set_defaults(func=cmd_list)

    # build
    sub = subparsers.add_parser("build", help="Build WARPXM")
    sub.add_argument(
        "--source-dir",
        default=str(DEFAULT_SOURCE_DIR),
        help="WARPXM source directory",
    )
    sub.add_argument(
        "--build-dir",
        default=str(DEFAULT_BUILD_DIR),
        help="WARPXM build directory",
    )
    sub.add_argument(
        "--build-type",
        default="Release",
        choices=["Release", "Debug", "RelWithDebInfo"],
    )
    sub.add_argument("--cmake-args", default=None, help="Extra cmake arguments")
    sub.add_argument("-j", "--jobs", type=int, default=None, help="Parallel build jobs")
    sub.set_defaults(func=cmd_build)

    # run
    sub = subparsers.add_parser("run", help="Run a benchmark")
    sub.add_argument(
        "benchmark",
        help="Benchmark name (e.g. 'advection')",
    )
    sub.add_argument(
        "-n",
        "--num-runs",
        type=int,
        default=3,
        help="Number of runs to average",
    )
    sub.add_argument(
        "--num-procs",
        type=int,
        default=0,
        help="MPI processes (0 = serial)",
    )
    sub.add_argument(
        "--source-dir",
        default=str(DEFAULT_SOURCE_DIR),
        help="WARPXM source directory",
    )
    sub.add_argument(
        "--build-dir",
        default=str(DEFAULT_BUILD_DIR),
        help="WARPXM build directory",
    )
    sub.add_argument(
        "--build-type",
        default="Release",
    )
    sub.add_argument(
        "--work-dir",
        default=None,
        help="Working directory for benchmark runs",
    )
    sub.set_defaults(func=cmd_run)

    # results
    sub = subparsers.add_parser("results", help="Show benchmark results")
    sub.add_argument("--benchmark", default=None, help="Filter by benchmark name")
    sub.add_argument("--hardware-id", default=None, help="Filter by hardware ID")
    sub.add_argument("--limit", type=int, default=20, help="Max results to show")
    sub.set_defaults(func=cmd_results)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
