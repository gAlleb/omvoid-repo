#!/usr/bin/env python3
"""Update opted-in templates from stable GitHub source tags."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request

VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def literal(text, name, pattern):
    values = re.findall(rf"^{name}=({pattern})$", text, re.MULTILINE)
    if len(values) != 1:
        raise ValueError(f"Expected one literal {name} assignment")
    return values[0]


def latest_tag(tags, prefix="v"):
    versions = []
    for line in tags.splitlines():
        match = re.fullmatch(rf"[0-9a-f]+\s+refs/tags/{re.escape(prefix)}({VERSION})(?:\^\{{\}})?", line)
        if match:
            versions.append(match[1])
    if not versions:
        raise ValueError("No stable version tags found")
    return max(versions, key=lambda value: tuple(map(int, value.split('.'))))


def validate_archive(archive, version, package, rule):
    with tarfile.open(fileobj=archive, mode="r:gz") as tar:
        roots = {member.name.split('/')[0] for member in tar.getmembers()}
        if len(roots) != 1:
            raise ValueError("Expected a source archive with one root directory")
        prefix = roots.pop() + "/"
        if "cargo_manifest" not in rule:
            return
        def read(name):
            member = tar.getmember(prefix + name)
            if not member.isfile() or member.size > 4 * 1024 * 1024:
                raise ValueError(f"Invalid source metadata: {name}")
            return tar.extractfile(member).read().decode()

        workspace = tomllib.loads(read("Cargo.toml"))
        metadata = tomllib.loads(read(rule["cargo_manifest"]))["package"]
        package_version = metadata["version"]
        if package_version == {"workspace": True}:
            package_version = workspace["workspace"]["package"]["version"]
        if metadata["name"] != package or package_version != version:
            raise ValueError("The tag does not match the Cargo package version")
        lock = tomllib.loads(read("Cargo.lock"))
        if not any(p["name"] == package and p["version"] == version for p in lock["package"]):
            raise ValueError("Cargo.lock does not match the tag")
        for patch in workspace.get("patch", {}).get("crates-io", {}).values():
            if isinstance(patch, dict) and "path" in patch:
                read(patch["path"] + "/Cargo.toml")


def open_source_archive(url):
    print(f"Downloading {url}", flush=True)
    for attempt in range(1, 5):
        try:
            return urllib.request.urlopen(url, timeout=60)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in (500, 502, 503, 504) or attempt == 4:
                raise
            delay = 2 ** attempt
            print(f"HTTP {error.code} for {url}; retrying in {delay}s", flush=True)
            time.sleep(delay)


def archive_url(rule, version):
    return f"https://codeload.github.com/{rule['repository']}/tar.gz/refs/tags/{rule['tag_prefix']}{version}"


def source_checksum(version, package, rule):
    url = archive_url(rule, version)
    digest = hashlib.sha256()
    with tempfile.TemporaryFile() as archive:
        with open_source_archive(url) as response:
            while chunk := response.read(1024 * 1024):
                archive.write(chunk)
                digest.update(chunk)
        archive.seek(0)
        validate_archive(archive, version, package, rule)
    return digest.hexdigest()


def update(template, tags, rule):
    text = template.read_text()
    package = template.parent.name
    expected_url = f'https://github.com/{rule["repository"]}/archive/refs/tags/{rule["tag_prefix"]}${{version}}.tar.gz'
    old_distfiles = f'distfiles="{expected_url}"'
    direct_url = archive_url(rule, '${version}')
    distfiles = f'distfiles="{direct_url}>${{pkgname}}-${{version}}.tar.gz"'
    if not {distfiles, old_distfiles}.intersection(text.splitlines()):
        raise ValueError(f"{package}: distfiles does not match the update rule")
    current = literal(text, "version", VERSION)
    literal(text, "revision", r"[1-9][0-9]*")
    literal(text, "checksum", r"[0-9a-f]{64}")
    latest = latest_tag(tags, rule["tag_prefix"])
    if tuple(map(int, latest.split('.'))) <= tuple(map(int, current.split('.'))):
        print(f"{package} {current}: no newer stable tag")
        return False
    checksum = source_checksum(latest, package, rule)
    for name, value in (("version", latest), ("revision", "1"), ("checksum", checksum)):
        text = re.sub(rf"^{name}=.*$", f"{name}={value}", text, count=1, flags=re.MULTILINE)
    template.write_text(text.replace(old_distfiles, distfiles))
    print(f"Updated {package}: {current} -> {latest}")
    return True


def read_rule(path):
    rule = json.loads(path.read_text())
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", rule["repository"]):
        raise ValueError(f"Invalid upstream repository in {path}")
    if not re.fullmatch(r"[A-Za-z0-9._-]*", rule["tag_prefix"]):
        raise ValueError(f"Invalid tag prefix in {path}")
    return rule


if __name__ == "__main__":
    updated = []
    for path in sorted(Path("srcpkgs").glob("*/update.json")):
        rule = read_rule(path)
        upstream = f"https://github.com/{rule['repository']}.git"
        tags = subprocess.check_output(["git", "ls-remote", "--tags", upstream], text=True, timeout=60)
        if update(path.with_name("template"), tags, rule):
            updated.append(path.parent.name)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            stream.write(f"updated={json.dumps(updated)}\n")
