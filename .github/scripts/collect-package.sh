#!/bin/bash
set -euo pipefail

package=${1:?Expected a package name}
artifacts=$(realpath "${2:?Expected an artifact directory}")
[[ $package =~ ^[A-Za-z0-9][A-Za-z0-9+_.-]*$ ]] || exit 1
packages=()
# Restricted packages may be placed in a subrepository by xbps-src.
while IFS= read -r -d '' file; do
  pkgver=$(xbps-uhelper binpkgver "$file")
  if [[ $(xbps-uhelper getpkgname "$pkgver") == "$package" ]]; then
    packages+=("$file")
  fi
done < <(find void-packages/hostdir/binpkgs -type f -name '*.xbps' -print0)
if ((${#packages[@]} != 1)); then
  echo "Expected one $package package, found ${#packages[@]}" >&2
  exit 1
fi
cp "${packages[0]}" "$artifacts/"
printf '%s\n' "$package" > "$artifacts/PACKAGE"
cd "$artifacts"
sha256sum ./*.xbps > SHA256SUMS
