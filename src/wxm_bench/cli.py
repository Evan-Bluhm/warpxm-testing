"""Command-line interface for wxm-bench."""

import argparse
from pathlib import Path

from . import benchmarks, builder, hardware, runner
from . import config as cfg
from . import database as db

_DEFAULT_SOURCE_DIR = str(Path.home() / "GitHub" / "warpxm")
_DEFAULT_BUILD_DIR = str(Path.home() / "GitHub" / "warpxm" / "build")
_DEFAULT_GRAFANA_PROV_DIR = (
    "/opt/homebrew/Cellar/grafana/12.3.3/share/grafana/conf/provisioning"
)
GRAFANA_DIR = Path(__file__).resolve().parent.parent.parent / "grafana"


def _resolve(args, attr: str, config: dict, *keys: str, default):
    """Return CLI arg if set, else config value, else hardcoded default.

    CLI args that were not provided have value None (sentinel).
    """
    cli_val = getattr(args, attr, None)
    if cli_val is not None:
        return cli_val
    config_val = cfg.get(config, *keys)
    if config_val is not None:
        return config_val
    return default


def cmd_hw_info(args):
    """Print detected hardware information."""
    cpu_ov = cfg.get(args._config, "hardware", "cpu")
    gpu_ov = cfg.get(args._config, "hardware", "gpu")
    info = hardware.get_hardware_info(cpu_override=cpu_ov, gpu_override=gpu_ov)
    print(f"CPU:         {info['cpu']}")
    print(f"GPU:         {info['gpu']}")
    print(f"Hardware ID: {info['hardware_id']}")
    if cpu_ov or gpu_ov:
        print()
        if cpu_ov:
            print(f"  (CPU from config, auto-detected: {hardware.get_cpu_name()})")
        if gpu_ov:
            print(f"  (GPU from config, auto-detected: {hardware.get_gpu_name()})")


def cmd_init_db(args):
    """Initialize the database."""
    db_path = Path(args.db)
    db.init_db(db_path)
    print(f"Database initialized at {db_path}")


def cmd_build(args):
    """Build WARPXM."""
    config = args._config
    source_dir = Path(
        _resolve(
            args,
            "source_dir",
            config,
            "paths",
            "source_dir",
            default=_DEFAULT_SOURCE_DIR,
        )
    )
    build_dir = Path(
        _resolve(
            args, "build_dir", config, "paths", "build_dir", default=_DEFAULT_BUILD_DIR
        )
    )
    build_type = _resolve(
        args, "build_type", config, "build", "build_type", default="Release"
    )

    cmake_args_str = _resolve(
        args, "cmake_args", config, "build", "cmake_args", default=None
    )
    extra_args = cmake_args_str.split() if cmake_args_str else None

    jobs = _resolve(args, "jobs", config, "build", "jobs", default=None)

    print(f"Building WARPXM from {source_dir}")
    print(f"Build directory: {build_dir}")
    print(f"Build type: {build_type}")

    info = builder.build_warpxm(
        source_dir=source_dir,
        build_dir=build_dir,
        build_type=build_type,
        extra_cmake_args=extra_args,
        jobs=jobs,
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
    config = args._config
    db_path = Path(args.db)
    db.init_db(db_path)
    conn = db.get_connection(db_path)

    source_dir = Path(
        _resolve(
            args,
            "source_dir",
            config,
            "paths",
            "source_dir",
            default=_DEFAULT_SOURCE_DIR,
        )
    )
    build_dir = Path(
        _resolve(
            args, "build_dir", config, "paths", "build_dir", default=_DEFAULT_BUILD_DIR
        )
    )
    build_type = _resolve(
        args, "build_type", config, "build", "build_type", default="Release"
    )

    # Resolve build
    warpxm_exec = builder.get_warpxm_exec(build_dir)
    git_info = builder.get_git_info(source_dir)

    # Find or create a build record
    existing = db.find_build(conn, git_info["sha"], build_type)
    if existing:
        build_id = existing["id"]
        print(f"Using existing build record (build_id={build_id})")
    else:
        build_id = db.insert_build(
            conn,
            git_sha=git_info["sha"],
            git_branch=git_info["branch"],
            build_type=build_type,
            cmake_args=None,
        )
        print(f"Created build record (build_id={build_id})")

    # Resolve the input file
    benchmark_name = args.benchmark
    input_file = benchmarks.get_input_file(benchmark_name)

    work_dir_str = _resolve(args, "work_dir", config, "paths", "work_dir", default=None)
    work_dir = Path(work_dir_str) if work_dir_str else Path.cwd() / "benchmark_runs"
    benchmark_work_dir = work_dir / benchmark_name
    benchmark_work_dir.mkdir(parents=True, exist_ok=True)

    num_runs = _resolve(args, "num_runs", config, "run", "num_runs", default=3)
    num_procs = _resolve(
        args, "num_procs", config, "run", "num_procs_single", default=0
    )
    mpi_launcher = cfg.get(config, "run", "mpi_launcher") or "mpiexec"
    cpu_ov = cfg.get(config, "hardware", "cpu")
    gpu_ov = cfg.get(config, "hardware", "gpu")

    # Run it
    result = runner.run_benchmark_averaged(
        benchmark_name=benchmark_name,
        input_file=input_file,
        warpxm_exec=warpxm_exec,
        build_id=build_id,
        conn=conn,
        num_runs=num_runs,
        num_procs=num_procs,
        mpirun=mpi_launcher,
        work_dir=benchmark_work_dir,
        git_sha=git_info["sha"],
        cpu_override=cpu_ov,
        gpu_override=gpu_ov,
    )

    conn.close()
    return result


def cmd_run_all(args):
    """Run all benchmarks at each specified process count."""
    config = args._config
    db_path = Path(args.db)
    db.init_db(db_path)
    conn = db.get_connection(db_path)

    source_dir = Path(
        _resolve(
            args,
            "source_dir",
            config,
            "paths",
            "source_dir",
            default=_DEFAULT_SOURCE_DIR,
        )
    )
    build_dir = Path(
        _resolve(
            args, "build_dir", config, "paths", "build_dir", default=_DEFAULT_BUILD_DIR
        )
    )
    build_type = _resolve(
        args, "build_type", config, "build", "build_type", default="Release"
    )

    warpxm_exec = builder.get_warpxm_exec(build_dir)
    git_info = builder.get_git_info(source_dir)

    existing = db.find_build(conn, git_info["sha"], build_type)
    if existing:
        build_id = existing["id"]
        print(f"Using existing build record (build_id={build_id})")
    else:
        build_id = db.insert_build(
            conn,
            git_sha=git_info["sha"],
            git_branch=git_info["branch"],
            build_type=build_type,
            cmake_args=None,
        )
        print(f"Created build record (build_id={build_id})")

    num_procs_str = _resolve(
        args, "num_procs", config, "run", "num_procs", default="0,6"
    )
    proc_counts = [int(p) for p in str(num_procs_str).split(",")]
    num_runs = _resolve(args, "num_runs", config, "run", "num_runs", default=3)
    mpi_launcher = cfg.get(config, "run", "mpi_launcher") or "mpiexec"
    cpu_ov = cfg.get(config, "hardware", "cpu")
    gpu_ov = cfg.get(config, "hardware", "gpu")

    all_benchmarks = benchmarks.list_benchmarks()
    work_dir_str = _resolve(args, "work_dir", config, "paths", "work_dir", default=None)
    work_dir = Path(work_dir_str) if work_dir_str else Path.cwd() / "benchmark_runs"

    if not all_benchmarks:
        print("No benchmark .inp files found.")
        conn.close()
        return

    print(
        f"Running {len(all_benchmarks)} benchmark(s) x {len(proc_counts)} process count(s)"
    )
    print(f"  Benchmarks:     {', '.join(all_benchmarks)}")
    print(f"  Process counts: {proc_counts}")
    print()

    for name in all_benchmarks:
        input_file = benchmarks.get_input_file(name)
        for np in proc_counts:
            label = f"{name}/np={np}" if np > 0 else f"{name}/serial"
            print(f"\n{'=' * 60}")
            print(f"  {label}")
            print(f"{'=' * 60}")

            benchmark_work_dir = work_dir / name
            benchmark_work_dir.mkdir(parents=True, exist_ok=True)

            runner.run_benchmark_averaged(
                benchmark_name=name,
                input_file=input_file,
                warpxm_exec=warpxm_exec,
                build_id=build_id,
                conn=conn,
                num_runs=num_runs,
                num_procs=np,
                mpirun=mpi_launcher,
                work_dir=benchmark_work_dir,
                git_sha=git_info["sha"],
                cpu_override=cpu_ov,
                gpu_override=gpu_ov,
            )

    conn.close()


def cmd_list(args):
    """List available benchmarks."""
    names = benchmarks.list_benchmarks()
    if not names:
        print("No benchmark .inp files found.")
        return
    for name in names:
        print(name)


def cmd_setup_grafana(args):
    """Set up Grafana datasource and dashboard provisioning."""
    config = args._config
    prov_dir = Path(
        _resolve(
            args,
            "grafana_provisioning_dir",
            config,
            "grafana",
            "provisioning_dir",
            default=_DEFAULT_GRAFANA_PROV_DIR,
        )
    )
    db_path = Path(args.db).resolve()

    # Ensure DB exists
    db.init_db(db_path)

    # --- Datasource ---
    ds_src = GRAFANA_DIR / "datasources" / "warpxm-benchmarks.yaml"
    ds_content = ds_src.read_text().replace("__BENCHMARKS_DB_PATH__", str(db_path))

    ds_dest = prov_dir / "datasources" / "warpxm-benchmarks.yaml"
    ds_dest.parent.mkdir(parents=True, exist_ok=True)
    ds_dest.write_text(ds_content)
    print(f"Wrote datasource: {ds_dest}")

    # --- Dashboard provider ---
    dashboards_dir = (GRAFANA_DIR / "dashboards").resolve()
    dp_src = GRAFANA_DIR / "dashboards" / "provider.yaml"
    dp_content = dp_src.read_text().replace(
        "__DASHBOARDS_DIR_PATH__", str(dashboards_dir)
    )

    dp_dest = prov_dir / "dashboards" / "warpxm-benchmarks.yaml"
    dp_dest.parent.mkdir(parents=True, exist_ok=True)
    dp_dest.write_text(dp_content)
    print(f"Wrote dashboard provider: {dp_dest}")

    print(f"\nDatasource DB path: {db_path}")
    print(f"Dashboard JSON dir: {dashboards_dir}")
    print("\nRestart Grafana to apply:")
    print("  brew services restart grafana")
    print("\nThen open: http://localhost:3000")


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
        prog="wxm-bench",
        description="Performance testing framework for WARPXM",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config file (default: wxm-bench.toml in project root)",
    )
    parser.add_argument(
        "--db",
        default=None,
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
    sub.add_argument("--source-dir", default=None, help="WARPXM source directory")
    sub.add_argument("--build-dir", default=None, help="WARPXM build directory")
    sub.add_argument(
        "--build-type",
        default=None,
        choices=["Release", "Debug", "RelWithDebInfo"],
    )
    sub.add_argument("--cmake-args", default=None, help="Extra cmake arguments")
    sub.add_argument("-j", "--jobs", type=int, default=None, help="Parallel build jobs")
    sub.set_defaults(func=cmd_build)

    # run
    sub = subparsers.add_parser("run", help="Run a benchmark")
    sub.add_argument("benchmark", help="Benchmark name (e.g. 'advection')")
    sub.add_argument(
        "-n", "--num-runs", type=int, default=None, help="Number of runs to average"
    )
    sub.add_argument(
        "--num-procs", type=int, default=None, help="MPI processes (0 = serial)"
    )
    sub.add_argument("--source-dir", default=None, help="WARPXM source directory")
    sub.add_argument("--build-dir", default=None, help="WARPXM build directory")
    sub.add_argument("--build-type", default=None)
    sub.add_argument(
        "--work-dir", default=None, help="Working directory for benchmark runs"
    )
    sub.set_defaults(func=cmd_run)

    # run-all
    sub = subparsers.add_parser("run-all", help="Run all benchmarks")
    sub.add_argument(
        "-n",
        "--num-runs",
        type=int,
        default=None,
        help="Number of runs to average per benchmark",
    )
    sub.add_argument(
        "--num-procs",
        default=None,
        help="Comma-separated list of MPI process counts (0 = serial)",
    )
    sub.add_argument("--source-dir", default=None, help="WARPXM source directory")
    sub.add_argument("--build-dir", default=None, help="WARPXM build directory")
    sub.add_argument("--build-type", default=None)
    sub.add_argument(
        "--work-dir", default=None, help="Working directory for benchmark runs"
    )
    sub.set_defaults(func=cmd_run_all)

    # results
    sub = subparsers.add_parser("results", help="Show benchmark results")
    sub.add_argument("--benchmark", default=None, help="Filter by benchmark name")
    sub.add_argument("--hardware-id", default=None, help="Filter by hardware ID")
    sub.add_argument("--limit", type=int, default=20, help="Max results to show")
    sub.set_defaults(func=cmd_results)

    # setup-grafana
    sub = subparsers.add_parser(
        "setup-grafana", help="Set up Grafana datasource and dashboard"
    )
    sub.add_argument(
        "--grafana-provisioning-dir",
        default=None,
        help="Grafana provisioning directory",
    )
    sub.set_defaults(func=cmd_setup_grafana)

    args = parser.parse_args()

    # Load config
    config_path = Path(args.config) if args.config else None
    args._config = cfg.load_config(config_path)

    # Resolve db path: CLI > config > default
    if args.db is None:
        args.db = cfg.get(args._config, "paths", "db") or str(db.DEFAULT_DB_PATH)

    args.func(args)


if __name__ == "__main__":
    main()
