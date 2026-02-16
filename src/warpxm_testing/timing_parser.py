"""Parse WARPXM timing report output.

WARPXM prints a hierarchical timing table like:

    Timing report (running total since start of sim)
    Total time elapsed (ms) = 1 234.56
    ==================================================
    | Scope                      | Time elapsed (ms) | % of total |
    ==================================================
    | rk_solver/step             |            123.45 |      45.62 |
    | ....variable_adjusters     |             67.89 |      25.10 |
    ==================================================

Nesting is indicated by leading dots (4 per level). We reconstruct
the full scope path (e.g. "rk_solver/step/variable_adjusters").
"""

import re

# Matches a row like: | ....scope_name   |        123.45 |      45.62 |
_ROW_RE = re.compile(
    r"^\|\s+"
    r"(\.*)(\S.*?)"  # dots + scope name
    r"\s+\|\s+"
    r"([\d ,]+\.?\d*)"  # time elapsed (ms), may have thousands separators
    r"\s+\|\s+"
    r"([\d.]+)"  # percent of total
    r"\s+\|$"
)

_TOTAL_RE = re.compile(r"Total time elapsed \(ms\)\s*=\s*([\d ,]+\.?\d*)")

_FRAME_RE = re.compile(
    r"Advanced from frame \d+ to \d+ in ([\d.]+(?:e[+-]?\d+)?)\s+seconds?"
)


def parse_timing_report(output: str) -> dict:
    """Parse the last timing report from WARPXM output.

    Returns:
        {
            "total_ms": float,
            "scopes": [{"scope": str, "elapsed_ms": float, "percent_total": float}, ...],
            "frame_times_s": [float, ...],
        }
    """
    lines = output.splitlines()

    # Find the LAST timing report (the final one at end of sim is authoritative)
    last_report_start = None
    for i, line in enumerate(lines):
        if "Timing report" in line:
            last_report_start = i

    total_ms = 0.0
    scopes = []
    frame_times = []

    # Parse frame advance times from entire output
    for line in lines:
        m = _FRAME_RE.search(line)
        if m:
            frame_times.append(float(m.group(1)))

    if last_report_start is None:
        return {"total_ms": total_ms, "scopes": scopes, "frame_times_s": frame_times}

    # Parse from the last report onward
    scope_stack: list[str] = []
    for line in lines[last_report_start:]:
        m = _TOTAL_RE.search(line)
        if m:
            total_ms = _parse_number(m.group(1))
            continue

        m = _ROW_RE.match(line)
        if m:
            dots, name, time_str, pct_str = m.groups()
            depth = len(dots) // 4
            name = name.strip()

            # Adjust scope stack to current depth
            scope_stack = scope_stack[:depth]
            scope_stack.append(name)

            full_scope = "/".join(scope_stack)
            elapsed_ms = _parse_number(time_str)
            percent_total = float(pct_str)

            scopes.append(
                {
                    "scope": full_scope,
                    "elapsed_ms": elapsed_ms,
                    "percent_total": percent_total,
                }
            )

    return {
        "total_ms": total_ms,
        "scopes": scopes,
        "frame_times_s": frame_times,
    }


def _parse_number(s: str) -> float:
    """Parse a number that may have thousands separators (spaces or commas)."""
    return float(s.replace(" ", "").replace(",", ""))
