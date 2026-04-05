#!/usr/bin/env bash
# build.sh — Build the Claude Dashboard.
#
# Steps:
#   1. Download Bootstrap into www/bootstrap/ (if not present).
#   2. Build the Rust WASM module via wasm-pack.
#   3. Run generate-data.py to produce www/data.cbor and www/index.html.
#
# Prerequisites: rustup, wasm-pack, python3, cbor2 (pip install cbor2)

set -euo pipefail

SELFDIR=$(dirname "$(readlink -f "$0")")
WWW="$SELFDIR/www"
BOOTSTRAP_VERSION="5.3.3"
BOOTSTRAP_DIR="$WWW/bootstrap"

# ---------------------------------------------------------------------------
# 1. Bootstrap local mirror
# ---------------------------------------------------------------------------

if [ ! -f "$BOOTSTRAP_DIR/css/bootstrap.min.css" ]; then
  echo "Downloading Bootstrap $BOOTSTRAP_VERSION..."
  mkdir -p "$BOOTSTRAP_DIR/css" "$BOOTSTRAP_DIR/js"
  BASE="https://cdn.jsdelivr.net/npm/bootstrap@${BOOTSTRAP_VERSION}/dist"
  curl -sSfL "$BASE/css/bootstrap.min.css"     -o "$BOOTSTRAP_DIR/css/bootstrap.min.css"
  curl -sSfL "$BASE/css/bootstrap.min.css.map" -o "$BOOTSTRAP_DIR/css/bootstrap.min.css.map"
  curl -sSfL "$BASE/js/bootstrap.bundle.min.js"     -o "$BOOTSTRAP_DIR/js/bootstrap.bundle.min.js"
  curl -sSfL "$BASE/js/bootstrap.bundle.min.js.map" -o "$BOOTSTRAP_DIR/js/bootstrap.bundle.min.js.map"
  echo "Bootstrap downloaded."
else
  echo "Bootstrap already present, skipping download."
fi

# ---------------------------------------------------------------------------
# 2. Rust WASM build
# ---------------------------------------------------------------------------

echo "Building WASM module..."
cd "$SELFDIR"
wasm-pack build --target web --release --out-dir www/pkg
echo "WASM build done."

# ---------------------------------------------------------------------------
# 3. Data generation
# ---------------------------------------------------------------------------

echo "Generating dashboard data..."
python3 "$SELFDIR/generate-data.py"
echo "Data generation done."

echo ""
echo "Build complete. Serve the dashboard:"
echo "  cd $WWW && python3 -m http.server 8042"
echo "Or configure nginx using nginx/dashboard.conf."
