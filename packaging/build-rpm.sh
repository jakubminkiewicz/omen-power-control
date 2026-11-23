#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'Usage: %s VERSION (for example, 0.1.0)\n' "$0" >&2
    exit 1
fi

version=$1
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
name=omen-power-control
source_name=$name-$version
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/SOURCES"

tar -C "$project" -czf "$work/SOURCES/$source_name.tar.gz" \
    --owner=0 --group=0 --numeric-owner \
    --transform "s|^|$source_name/|" \
    --exclude=__pycache__ --exclude='*.rpm' --exclude='*.pyc' --exclude='*.ko' \
    --exclude='*.key' --exclude='*.pem' --exclude='*.der' \
    LICENSE LICENSES README.md docs app service packaging

rpmbuild -bb --define "_topdir $work" --define '_buildhost localhost' --define "omen_version $version" \
    "$project/packaging/$name.spec"

mapfile -t packages < <(find "$work/RPMS" -type f -name "$name-*.noarch.rpm")
if ((${#packages[@]} != 1)); then
    printf 'Expected one built OMEN Power Control RPM.\n' >&2
    exit 1
fi
out="$project/dist/$version"
mkdir -p "$out"
install -m0644 "${packages[0]}" "$out/"
(
    cd "$out"
    sha256sum -- "${packages[0]##*/}" > SHA256SUMS
)
printf 'Release assets are in %s\n' "$out"
