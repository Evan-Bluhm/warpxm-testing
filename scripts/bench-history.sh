#!/usr/bin/env bash
#
# Run benchmarks across the last N commits on master.
# Usage: ./scripts/bench-history.sh [NUM_COMMITS] [NUM_RUNS]
#
# Defaults: 30 commits, 3 runs per benchmark
#
# Reads source_dir and build_dir from wxm-bench.toml if present,
# falling back to ~/GitHub/warpxm.
#
set -euo pipefail

# Derive project root from script location
TESTING_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="$TESTING_DIR/wxm-bench.toml"

# Extract a simple key = "value" from a TOML file.
# Usage: toml_get <file> <section> <key>
toml_get() {
    local file="$1" section="$2" key="$3"
    if [ ! -f "$file" ]; then
        return 1
    fi
    # Find the section, then extract the key's value
    sed -n "/^\[$section\]/,/^\[/p" "$file" \
        | grep "^${key}[[:space:]]*=" \
        | head -1 \
        | sed 's/^[^=]*=[[:space:]]*"\(.*\)"/\1/' \
        | sed "s|^~|$HOME|"
}

# Read paths from config, falling back to defaults
WARPXM_SRC=$(toml_get "$CONFIG_FILE" "paths" "source_dir" 2>/dev/null || echo "$HOME/GitHub/warpxm")
WARPXM_BUILD=$(toml_get "$CONFIG_FILE" "paths" "build_dir" 2>/dev/null || echo "$HOME/GitHub/warpxm/build")

NUM_COMMITS="${1:-30}"
NUM_RUNS="${2:-3}"
NUM_PROCS=$(toml_get "$CONFIG_FILE" "run" "num_procs" 2>/dev/null || echo "0,6")

cd "$WARPXM_SRC"

# Save starting ref so we can restore it at the end
ORIGINAL_REF=$(git rev-parse HEAD)
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "detached")

# Collect the commit SHAs (newest first)
mapfile -t COMMITS < <(git log --format="%H" -n "$NUM_COMMITS" master)

echo "========================================"
echo " WARPXM Benchmark History"
echo "========================================"
echo "  Source:      $WARPXM_SRC"
echo "  Build:       $WARPXM_BUILD"
echo "  Testing:     $TESTING_DIR"
echo "  Config:      $CONFIG_FILE"
echo "  Commits:     ${#COMMITS[@]}"
echo "  Runs/bench:  $NUM_RUNS"
echo "  Proc counts: $NUM_PROCS"
echo "  Started:     $(date)"
echo "========================================"
echo

cleanup() {
    echo
    echo "Restoring original checkout: $ORIGINAL_REF ($ORIGINAL_BRANCH)"
    cd "$WARPXM_SRC"
    if [ "$ORIGINAL_BRANCH" != "detached" ]; then
        git checkout "$ORIGINAL_BRANCH" --quiet
    else
        git checkout "$ORIGINAL_REF" --quiet
    fi
}
trap cleanup EXIT

# Detect CPU count portably
if command -v nproc &>/dev/null; then
    NJOBS=$(nproc)
else
    NJOBS=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
fi

for i in "${!COMMITS[@]}"; do
    SHA="${COMMITS[$i]}"
    SHORT_SHA="${SHA:0:10}"
    N=$((i + 1))

    echo "========================================"
    echo " [$N/${#COMMITS[@]}] Commit $SHORT_SHA"
    echo "========================================"

    # Checkout the commit
    git checkout "$SHA" --quiet
    echo "  Checked out: $(git log --oneline -1)"

    # Build
    echo "  Building..."
    if ! cmake --build "$WARPXM_BUILD" "-j$NJOBS" > "/tmp/warpxm-build-$SHORT_SHA.log" 2>&1; then
        echo "  BUILD FAILED — skipping (see /tmp/warpxm-build-$SHORT_SHA.log)"
        echo
        continue
    fi
    echo "  Build OK"

    # Run benchmarks
    echo "  Running benchmarks..."
    cd "$TESTING_DIR"
    if ! uv run wxm-bench run-all -n "$NUM_RUNS" --num-procs "$NUM_PROCS"; then
        echo "  BENCHMARK FAILED — continuing to next commit"
    fi
    cd "$WARPXM_SRC"

    echo "  Done with $SHORT_SHA"
    echo
done

echo "========================================"
echo " Finished: $(date)"
echo "========================================"
