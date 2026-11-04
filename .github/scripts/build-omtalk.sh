#!/bin/bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../../void-packages"

# A fresh build root keeps local packages and host toolchains out of the result.
./xbps-src binary-bootstrap
# -Q runs the template's check phase; do not publish a package after failed tests.
./xbps-src -Q -j2 pkg omtalk
