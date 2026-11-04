#!/bin/bash
set -euo pipefail
shopt -s nullglob

repo=$(realpath "${1:?Usage: publish-packages.sh REPOSITORY ARTIFACTS}")
artifacts=$(realpath "${2:?Usage: publish-packages.sh REPOSITORY ARTIFACTS}")
branch=repository-x86_64-glibc
export XBPS_ARCH=x86_64

fail() {
  echo "::error::$*" >&2
  exit 1
}

[[ -n ${OMVOID_REPO_KEY:-} ]] || fail 'OMVOID_REPO_KEY is missing'
[[ -z $(git -C "$repo" status --porcelain) ]] || fail 'Binary checkout is not clean'
expected=$(git -C "$repo" rev-parse HEAD)
# Every successful matrix entry must supply exactly one named artifact.
python3 - "$artifacts" <<'PY'
import json, os, pathlib, re, sys
expected = json.loads(os.environ['EXPECTED_PACKAGES'])
names = [p.read_text().strip() for p in pathlib.Path(sys.argv[1]).glob('*/PACKAGE')]
if (not names or len(names) != len(set(names)) or sorted(names) != sorted(expected)
        or not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+_.-]*', name) for name in names)):
    sys.exit('Artifacts do not match the selected packages')
PY
packages=()
pkgvers=()
names=()
for manifest in "$artifacts"/*/PACKAGE; do
  directory=$(dirname "$manifest")
  name=$(cat "$manifest")
  candidates=("$directory"/*.xbps)
  ((${#candidates[@]} == 1)) || fail "Expected one package in $directory"
  package=${candidates[0]}
  pkgver=$(xbps-uhelper binpkgver "$package")
  [[ $(xbps-uhelper getpkgname "$pkgver") == "$name" ]] || fail 'Unexpected package name'
  arch=$(xbps-uhelper binpkgarch "$package")
  [[ $arch == x86_64 || $arch == noarch ]] || fail "Unsupported package architecture: $arch"
  (cd "$directory" && sha256sum --check SHA256SUMS)
  current=$(xbps-query -i --repository="$repo" -p pkgver "$name" 2>/dev/null || true)
  if [[ -n $current ]]; then
    version=$(xbps-uhelper getpkgversion "$current")
    # xbps-uhelper returns 1 for a match and 0 for a mismatch.
    match=0
    xbps-uhelper pkgmatch "$pkgver" "$name>=$version" || match=$?
    [[ $match == 1 ]] || fail "Refusing to replace $current with $pkgver"
  fi
  packages+=("$package")
  pkgvers+=("$pkgver")
  names+=("$name")
done

# Keep the key outside both Git checkouts and remove it even on failure.
umask 077
private_dir=$(mktemp -d "${RUNNER_TEMP:-/tmp}/omvoid-sign.XXXXXX")
trap 'rm -rf -- "$private_dir"' EXIT
printf '%s\n' "$OMVOID_REPO_KEY" > "$private_dir/key.pem"
unset OMVOID_REPO_KEY
openssl pkey -in "$private_dir/key.pem" -passin pass: -pubout > "$private_dir/public.pem"
bsdtar -xOf "$repo/x86_64-repodata" index-meta.plist |
  python3 -c 'import plistlib, sys; sys.stdout.buffer.write(plistlib.loads(sys.stdin.buffer.read())["public-key"])' > "$private_dir/current-public.pem"
cmp -s "$private_dir/public.pem" "$private_dir/current-public.pem" ||
  fail 'Signing key does not match the existing repository'
umask 022

new_packages=()
for package in "${packages[@]}"; do
  target="$repo/$(basename "$package")"
  cp "$package" "$target"
  # Rebuilds may keep the same version; the previous signature then no longer applies.
  rm -f -- "$target.sig2"
  new_packages+=("$target")
done
xbps-rindex -fa "${new_packages[@]}"
for index in "${!names[@]}"; do
  [[ $(xbps-query -i --repository="$repo" -p pkgver "${names[$index]}") == "${pkgvers[$index]}" ]] ||
    fail "${names[$index]} did not enter the repository index"
done
xbps-rindex -r "$repo"
xbps-rindex --privkey "$private_dir/key.pem" --sign --signedby Stefan "$repo"
xbps-rindex --privkey "$private_dir/key.pem" --sign-pkg "$repo"/*.xbps
for file in "$repo"/*.xbps; do
  openssl dgst -sha256 -verify "$private_dir/public.pem" -signature "$file.sig2" "$file"
done

git -C "$repo" config user.name 'github-actions[bot]'
git -C "$repo" config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git -C "$repo" add -A
tree=$(git -C "$repo" write-tree)
# No -p: each publication replaces the binary history with one complete snapshot.
commit=$(printf 'Publish %s\n' "${pkgvers[*]}" | git -C "$repo" commit-tree "$tree")
git -C "$repo" push --force-with-lease="refs/heads/$branch:$expected" \
  origin "$commit:refs/heads/$branch"
