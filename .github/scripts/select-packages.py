#!/usr/bin/env python3
"""Select changed templates or retry unpublished scheduled updates."""
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")


def available_packages():
    return {path.parent.name for path in Path("srcpkgs").glob("*/template") if NAME.fullmatch(path.parent.name)}


def changed_packages(before, available):
    if not re.fullmatch(r"[0-9a-f]{40}", before) or set(before) == {"0"}:
        return sorted(available)
    exists = subprocess.run(["git", "cat-file", "-e", before], capture_output=True).returncode == 0
    if not exists:
        fetched = subprocess.run(["git", "fetch", "--no-tags", "--depth=1", "origin", before]).returncode == 0
        if not fetched:
            print("::warning::Push base is unavailable; building all templates")
            return sorted(available)
    files = subprocess.check_output(["git", "diff", "--name-only", "--no-renames", "-z", before, "HEAD"], text=True).split('\0')
    selected = {parts[1] for name in files if len(parts := name.split('/')) >= 3 and parts[0] == "srcpkgs" and parts[1] in available}
    return sorted(selected)


def is_published(package, repository):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository name")
    text = (Path("srcpkgs") / package / "template").read_text()
    version = re.search(r"^version=([0-9.]+)$", text, re.MULTILINE)[1]
    revision = re.search(r"^revision=([0-9]+)$", text, re.MULTILINE)[1]
    # Check an immutable snapshot, avoiding cached branch URLs and downloading packages.
    refs = subprocess.check_output(["git", "ls-remote", f"https://github.com/{repository}.git", "refs/heads/repository-x86_64-glibc"], text=True, timeout=60).split()
    if len(refs) != 2 or not re.fullmatch(r"[0-9a-f]{40}", refs[0]):
        raise ValueError("Cannot resolve the binary repository branch")
    for arch in ("x86_64", "noarch"):
        url = f"https://raw.githubusercontent.com/{repository}/{refs[0]}/{package}-{version}_{revision}.{arch}.xbps"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30):
                return True
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    return False


def select(event_name, event, requested, available, updated=()):
    if event_name == "schedule":
        return sorted(path.parent.name for path in Path("srcpkgs").glob("*/update.json") if not is_published(path.parent.name, os.environ["GITHUB_REPOSITORY"]))
    if event_name == "push":
        return changed_packages(event.get("before", ""), available)
    if event_name == "workflow_dispatch":
        selected = available.copy() if requested.strip() == "all" else set(requested.split())
        if updated:
            selected.update(updated)
        if not selected or not selected <= available:
            raise ValueError("Choose existing template names separated by spaces, or all")
        return sorted(selected)
    raise ValueError(f"Unsupported event: {event_name}")


if __name__ == "__main__":
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    packages = select(os.environ["GITHUB_EVENT_NAME"], event, os.environ.get("REQUESTED_PACKAGES", ""), available_packages(), json.loads(os.environ.get("UPDATED_PACKAGES") or "[]"))
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    print("Selected packages:", ", ".join(packages) or "none")
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"packages={json.dumps(packages)}\nsource={source}\n")
