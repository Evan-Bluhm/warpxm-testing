#!/usr/bin/env bash
#
# Run benchmarks across the last N commits on master.
# Usage: ./scripts/bench-history.sh [NUM_COMMITS] [NUM_RUNS]
#
# Defaults: 30 commits, 3 runs per benchmark
#
set -euo pipefail

WARPXM_SRC="$HOME/GitHub/warpxm"
WARPXM_BUILD="$HOME/GitHub/warpxm/build"
TESTING_DIR="$HOME/GitHub/warpxm-testing"

NUM_COMMITS="${1:-30}"
NUM_RUNS="${2:-3}"
NUM_PROCS="0,6"

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
    if ! cmake --build "$WARPXM_BUILD" -j$(sysctl -n hw.ncpu) > /tmp/warpxm-build-$SHORT_SHA.log 2>&1; then
        echo "  BUILD FAILED — skipping (see /tmp/warpxm-build-$SHORT_SHA.log)"
        echo
        continue
    fi
    echo "  Build OK"

    # Run benchmarks
    echo "  Running benchmarks..."
    cd "$TESTING_DIR"
    if ! uv run warpxm-test run-all -n "$NUM_RUNS" --num-procs "$NUM_PROCS"; then
        echo "  BENCHMARK FAILED — continuing to next commit"
    fi
    cd "$WARPXM_SRC"

    echo "  Done with $SHORT_SHA"
    echo
done

echo "========================================"
echo " Finished: $(date)"
echo "========================================"
