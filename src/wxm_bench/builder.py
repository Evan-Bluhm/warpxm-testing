"""Build WARPXM from source."""

import os
import subprocess
from pathlib import Path


def get_git_info(source_dir: Path) -> dict:
    """Get git SHA and branch from the WARPXM source directory."""

    def _git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=source_dir,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    return {
        "sha": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
    }


def configure(
    source_dir: Path,
    build_dir: Path,
    build_type: str = "Release",
    extra_cmake_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """Run cmake configure step."""
    build_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "cmake",
        f"-DCMAKE_BUILD_TYPE={build_type}",
        *(extra_cmake_args or []),
        str(source_dir),
    ]
    print(f"[configure] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=build_dir, check=True)


def build(
    build_dir: Path,
    jobs: int | None = None,
) -> subprocess.CompletedProcess:
    """Run cmake build step."""
    if jobs is None:
        jobs = os.cpu_count() or 4
    cmd = ["cmake", "--build", ".", f"-j{jobs}"]
    print(f"[build] {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=build_dir, check=True)


def get_warpxm_exec(build_dir: Path) -> Path:
    """Return path to the warpxm executable."""
    exe = build_dir / "bin" / "warpxm"
    if not exe.exists():
        raise FileNotFoundError(f"warpxm executable not found at {exe}")
    return exe


def get_warpy_dir(build_dir: Path) -> Path:
    """Return path to the warpy package in the build tree."""
    d = build_dir / "tools" / "warpy"
    if not d.exists():
        raise FileNotFoundError(f"warpy directory not found at {d}")
    return d


def build_warpxm(
    source_dir: Path,
    build_dir: Path,
    build_type: str = "Release",
    extra_cmake_args: list[str] | None = None,
    jobs: int | None = None,
) -> dict:
    """Full configure + build. Returns git info and paths."""
    git_info = get_git_info(source_dir)
    configure(source_dir, build_dir, build_type, extra_cmake_args)
    build(build_dir, jobs)
    return {
        "git_sha": git_info["sha"],
        "git_branch": git_info["branch"],
        "build_type": build_type,
        "cmake_args": " ".join(extra_cmake_args) if extra_cmake_args else None,
        "warpxm_exec": str(get_warpxm_exec(build_dir)),
        "warpy_dir": str(get_warpy_dir(build_dir)),
    }
