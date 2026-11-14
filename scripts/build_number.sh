#!/bin/bash
# Prints the CFBundleVersion for a version: major*10000 + minor*100 + patch (0.1.0 -> 100).
# Usage: scripts/build_number.sh [X.Y.Z]   (default: the repository's VERSION file)
# A pre-release suffix (-beta.1, +meta) is ignored. Minor and patch must stay below 100 so the
# number keeps increasing with the version, which Sparkle uses to order updates.
set -euo pipefail
v="${1:-$(tr -d '[:space:]' < "$(cd "$(dirname "$0")/.." && pwd)/VERSION")}"
core="${v%%[-+]*}"
[[ "$core" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || { echo "build_number: not X.Y.Z: '$v'" >&2; exit 1; }
major=$((10#${BASH_REMATCH[1]})); minor=$((10#${BASH_REMATCH[2]})); patch=$((10#${BASH_REMATCH[3]}))
[ "$minor" -lt 100 ] && [ "$patch" -lt 100 ] || { echo "build_number: minor and patch must be below 100: '$v'" >&2; exit 1; }
echo $((major * 10000 + minor * 100 + patch))
