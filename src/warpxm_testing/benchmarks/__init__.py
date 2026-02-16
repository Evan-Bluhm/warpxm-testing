"""Auto-discover benchmark .inp files from this directory."""

from pathlib import Path

BENCHMARKS_DIR = Path(__file__).parent


def list_benchmarks() -> list[str]:
    """Return sorted list of available benchmark names (filenames without .inp)."""
    return sorted(p.stem for p in BENCHMARKS_DIR.glob("*.inp"))


def get_input_file(name: str) -> Path:
    """Get the .inp file path for a benchmark name.

    The name can be either the full filename stem or a prefix that
    uniquely matches one .inp file.
    """
    # Exact match first
    exact = BENCHMARKS_DIR / f"{name}.inp"
    if exact.exists():
        return exact

    # Prefix match
    matches = [p for p in BENCHMARKS_DIR.glob("*.inp") if p.stem.startswith(name)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = [p.stem for p in matches]
        raise SystemExit(f"Ambiguous benchmark '{name}', matches: {', '.join(names)}")

    available = list_benchmarks()
    raise SystemExit(
        f"Unknown benchmark '{name}'. Available: {', '.join(available) or '(none)'}"
    )
