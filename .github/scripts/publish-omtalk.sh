#!/bin/bash
set -euo pipefail
shopt -s nullglob

repo=$(realpath "${1:?Usage: publish-omtalk.sh REPOSITORY ARTIFACTS}")
artifacts=$(realpath "${2:?Usage: publish-omtalk.sh REPOSITORY ARTIFACTS}")
branch=repository-x86_64-glibc
export XBPS_ARCH=x86_64

fail() {
  echo "::error::$*" >&2
  exit 1
}

[[ -n ${OMVOID_REPO_KEY:-} ]] || fail 'OMVOID_REPO_KEY is missing'
[[ -z $(git -C "$repo" status --porcelain) ]] || fail 'Binary checkout is not clean'
expected=$(git -C "$repo" rev-parse HEAD)
packages=("$artifacts"/*.xbps)
((${#packages[@]} == 1)) || fail 'Expected exactly one built package'
package=${packages[0]}
pkgver=$(xbps-uhelper binpkgver "$package")
[[ $(xbps-uhelper getpkgname "$pkgver") == omtalk ]] || fail 'Expected omtalk'
[[ $(xbps-uhelper binpkgarch "$package") == x86_64 ]] || fail 'Expected x86_64'
(cd "$artifacts" && sha256sum --check SHA256SUMS)

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

# Refuse an older source revision if a newer package was already published.
current=$(xbps-query -i --repository="$repo" -p pkgver omtalk 2>/dev/null || true)
if [[ -n $current ]]; then
  version=$(xbps-uhelper getpkgversion "$current")
  # xbps-uhelper returns 1 for a match and 0 for a mismatch.
  match=0
  xbps-uhelper pkgmatch "$pkgver" "omtalk>=$version" || match=$?
  [[ $match == 1 ]] ||
    fail "Refusing to replace $current with $pkgver"
fi

cp "$package" "$repo/"
# Rebuilds may keep the same version, so replace its index entry and signature.
rm -f -- "$repo/$(basename "$package").sig2"
xbps-rindex -fa "$repo/$(basename "$package")"
[[ $(xbps-query -i --repository="$repo" -p pkgver omtalk) == "$pkgver" ]] ||
  fail 'New package did not enter the repository index'
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
# No -p: the published snapshot has no parent, even on the first migration.
commit=$(printf 'Publish %s\n' "$pkgver" | git -C "$repo" commit-tree "$tree")
git -C "$repo" push --force-with-lease="refs/heads/$branch:$expected" \
  origin "$commit:refs/heads/$branch"
