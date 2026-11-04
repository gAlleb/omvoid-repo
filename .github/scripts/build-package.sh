#!/bin/bash
set -euo pipefail

package=${1:?Expected a package name}
[[ $package =~ ^[A-Za-z0-9][A-Za-z0-9+_.-]*$ ]] || exit 1

cd "$(dirname "${BASH_SOURCE[0]}")/../../void-packages"
[[ -f srcpkgs/$package/template ]] || exit 1

# A fresh build root keeps local packages and host toolchains out of the result.
./xbps-src binary-bootstrap
# -Q runs the template's check phase; do not publish a package after failed tests.
./xbps-src -Q -j2 pkg "$package"
