#!/usr/bin/env python3
"""Install pinned Geant4 data with checked archives and verifiable file receipts.

Uses only Python's standard library and curl. Damaged existing data is preserved
in quarantine. --verify-only never changes the data root; an explicitly requested
JSON report is its sole output. --manifest also permits small offline fixtures.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

DEFAULT_MANIFEST = Path(__file__).with_name("geant4-11.4.2-datasets.json")
DATASET_RECEIPT = ".geant4-dataset-receipt.json"
ROOT_RECEIPT = "geant4-datasets-receipt.json"
CHUNK = 1024 * 1024


class InstallError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest_file(path):
    if path.is_symlink() or not path.is_file():
        raise InstallError(f"Expected a regular file: {path}")
    md5, sha256 = hashlib.md5(), hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            md5.update(block)
            sha256.update(block)
    return {"md5": md5.hexdigest(), "sha256": sha256.hexdigest()}


def load_manifest(path):
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or not manifest.get("geant4_version")):
        raise InstallError("Unsupported or incomplete dataset manifest")
    entries = manifest.get("datasets")
    if not isinstance(entries, list) or not entries:
        raise InstallError("Dataset manifest is empty")
    seen = {key: set() for key in ("name", "env", "directory", "archive")}
    for entry in entries:
        if not isinstance(entry, dict):
            raise InstallError("Invalid dataset manifest entry")
        for key in ("name", "version", "env", "directory", "archive", "md5"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise InstallError(f"Dataset is missing {key}")
        for key in ("directory", "archive"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", entry[key]):
                raise InstallError(f"Unsafe {key}: {entry[key]}")
        if not re.fullmatch(r"G4[A-Z0-9_]+", entry["env"]):
            raise InstallError(f"Invalid dataset environment variable: {entry['env']}")
        if not re.fullmatch(r"[a-f0-9]{32}", entry["md5"]):
            raise InstallError(f"Invalid MD5 for {entry['name']}")
        for key, values in seen.items():
            if entry[key] in values:
                raise InstallError(f"Duplicate dataset {key}: {entry[key]}")
            values.add(entry[key])
    return manifest, hashlib.sha256(raw).hexdigest()


@contextlib.contextmanager
def root_lock(root, verify_only):
    lock = root / ".geant4-datasets.lock"
    if verify_only and not lock.exists():
        yield
        return
    if lock.is_symlink():
        raise InstallError(f"Refusing symlink lock: {lock}")
    with lock.open("rb" if verify_only else "a+b") as stream:
        try:
            operation = fcntl.LOCK_SH if verify_only else fcntl.LOCK_EX
            fcntl.flock(stream, operation | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallError("Another dataset installation or verification is active") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def quarantine(root, path):
    folder = root / ".quarantine"
    if folder.is_symlink():
        raise InstallError(f"Refusing symlink quarantine: {folder}")
    folder.mkdir(exist_ok=True)
    target = folder / f"{path.name}.{uuid.uuid4().hex}"
    os.replace(path, target)
    print(f"Preserved invalid data: {target}", flush=True)


def checked_archive(root, entry, base_url, verify_only):
    archive = root / entry["archive"]
    if archive.exists() or archive.is_symlink():
        try:
            digests = digest_file(archive)
            if digests["md5"] == entry["md5"]:
                return archive, digests
        except InstallError:
            pass
        if verify_only:
            raise InstallError(f"Archive MD5 verification failed: {archive}")
        quarantine(root, archive)
    elif verify_only:
        raise InstallError(f"Missing retained archive: {archive}")

    partial = root / f"{entry['archive']}.part"
    if partial.is_symlink() or (partial.exists() and not partial.is_file()):
        raise InstallError(f"Refusing non-regular partial download: {partial}")
    url = f"{base_url.rstrip('/')}/{entry['archive']}"
    for attempt in range(2):
        resume = partial.exists() and partial.stat().st_size > 0 and attempt == 0
        command = ["curl", "--fail", "--location", "--show-error", "--silent",
                   "--connect-timeout", "30", "--retry", "3", "--retry-delay", "2"]
        if resume:
            command.extend(["--continue-at", "-"])
        command.extend(["--output", str(partial), url])
        print(f"{'Resuming' if resume else 'Downloading'} {entry['archive']}", flush=True)
        result = subprocess.run(command, check=False)
        if result.returncode:
            if resume and result.returncode == 33:
                continue
            raise InstallError(f"curl failed ({result.returncode}); partial download retained: {partial}")
        digests = digest_file(partial)
        if digests["md5"] == entry["md5"]:
            os.replace(partial, archive)
            return archive, digests
        if not resume:
            break
        print(f"Partial archive did not match; retrying {entry['archive']} from the start", flush=True)
    raise InstallError(f"Downloaded archive MD5 does not match the pinned manifest: {partial}")


def dataset_identity(manifest, manifest_sha256, entry, digests):
    return {"schema_version": 1, "geant4_version": manifest["geant4_version"],
            "manifest_sha256": manifest_sha256, "dataset": entry,
            "archive_hashes": digests}


def verify_directory(directory, identity):
    if directory.is_symlink() or not directory.is_dir():
        raise InstallError(f"Missing regular dataset directory: {directory}")
    receipt_path = directory / DATASET_RECEIPT
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise InstallError(f"Missing dataset file receipt: {receipt_path}")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise InstallError(f"Invalid dataset file receipt: {receipt_path}") from exc
    if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in identity.items()):
        raise InstallError(f"Dataset receipt identity mismatch: {directory}")
    expected = receipt.get("files")
    if not isinstance(expected, dict) or not expected:
        raise InstallError(f"Empty dataset file manifest: {directory}")
    actual = {}
    for parent, directories, files in os.walk(directory, followlinks=False):
        for name in directories:
            if (Path(parent) / name).is_symlink():
                raise InstallError(f"Unexpected symlink in dataset: {Path(parent) / name}")
        for name in files:
            path = Path(parent) / name
            relative = path.relative_to(directory).as_posix()
            if relative == DATASET_RECEIPT:
                continue
            expected_file = expected.get(relative)
            if not isinstance(expected_file, dict) or path.is_symlink() or not path.is_file():
                raise InstallError(f"Unexpected dataset file: {path}")
            if path.stat().st_size != expected_file.get("size"):
                raise InstallError(f"Dataset file size mismatch: {path}")
            actual[relative] = {"size": path.stat().st_size,
                                "sha256": digest_file(path)["sha256"]}
            if actual[relative] != expected_file:
                raise InstallError(f"Dataset file SHA256 mismatch: {path}")
    if actual.keys() != expected.keys():
        missing = sorted(expected.keys() - actual.keys())
        raise InstallError(f"Dataset files missing from {directory}: {missing[:3]}")
    return {"file_count": len(actual), "receipt_sha256": digest_file(receipt_path)["sha256"]}


def extract_and_commit(root, archive, entry, identity):
    stage = Path(tempfile.mkdtemp(prefix=f".{entry['directory']}.extract-", dir=root))
    destination = root / entry["directory"]
    files = {}
    try:
        with tarfile.open(archive, "r:gz") as source:
            for member in source:
                parts = PurePosixPath(member.name).parts
                if (not parts or parts[0] != entry["directory"] or ".." in parts
                        or PurePosixPath(member.name).is_absolute()):
                    raise InstallError(f"Archive member outside expected dataset: {member.name}")
                path = stage.joinpath(*parts)
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile() or len(parts) < 2:
                    raise InstallError(f"Unsupported archive link or special file: {member.name}")
                relative = PurePosixPath(*parts[1:]).as_posix()
                if relative == DATASET_RECEIPT or relative in files:
                    raise InstallError(f"Duplicate or reserved archive member: {member.name}")
                path.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with source.extractfile(member) as incoming, path.open("xb") as outgoing:
                    for block in iter(lambda: incoming.read(CHUNK), b""):
                        outgoing.write(block)
                        digest.update(block)
                        size += len(block)
                if size != member.size:
                    raise InstallError(f"Truncated archive member: {member.name}")
                path.chmod(member.mode & 0o777)
                files[relative] = {"size": size, "sha256": digest.hexdigest()}
        if not files:
            raise InstallError(f"Archive contains no dataset files: {archive}")
        staged_directory = stage / entry["directory"]
        atomic_json(staged_directory / DATASET_RECEIPT,
                    {**identity, "installed_at": now(), "files": files})
        # A crash between these renames leaves no accepted partial directory.
        # Rerunning uses the checked archive; the old data remains in quarantine.
        if destination.exists() or destination.is_symlink():
            quarantine(root, destination)
        os.replace(staged_directory, destination)
        return {"file_count": len(files),
                "receipt_sha256": digest_file(destination / DATASET_RECEIPT)["sha256"]}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def install(args, report):
    manifest, manifest_hash = load_manifest(args.manifest)
    root = args.root.expanduser().resolve()
    report.update(geant4_version=manifest["geant4_version"], root=str(root),
                  manifest_sha256=manifest_hash, datasets=[], environment={})
    if args.verify_only:
        if not root.is_dir():
            raise InstallError(f"Dataset root does not exist: {root}")
    else:
        root.mkdir(parents=True, exist_ok=True)
    base_url = args.base_url or os.environ.get("G4_DATASET_URL") or manifest["base_url"]
    relative_environment = {entry["env"]: entry["directory"] for entry in manifest["datasets"]}
    with root_lock(root, args.verify_only):
        for entry in manifest["datasets"]:
            print(f"Checking {entry['directory']}", flush=True)
            archive, digests = checked_archive(root, entry, base_url, args.verify_only)
            identity = dataset_identity(manifest, manifest_hash, entry, digests)
            directory = root / entry["directory"]
            action = "verified" if args.verify_only else "reused"
            try:
                details = verify_directory(directory, identity)
            except InstallError:
                if args.verify_only:
                    raise
                action = "repaired" if directory.exists() or directory.is_symlink() else "installed"
                print(f"Installing checked archive: {entry['directory']}", flush=True)
                details = extract_and_commit(root, archive, entry, identity)
            report["datasets"].append({**entry, **details, "action": action,
                                       "path": str(directory), "archive_sha256": digests["sha256"]})
            report["environment"][entry["env"]] = str(directory)
            if not args.verify_only:
                atomic_json(root / ROOT_RECEIPT,
                            {"schema_version": 1, "geant4_version": manifest["geant4_version"],
                             "manifest_sha256": manifest_hash, "updated_at": now(),
                             "complete": len(report["datasets"]) == len(manifest["datasets"]),
                             "datasets": report["datasets"], "environment": report["environment"],
                             "environment_relative": relative_environment})
        if args.verify_only:
            receipt_path = root / ROOT_RECEIPT
            if receipt_path.is_symlink() or not receipt_path.is_file():
                raise InstallError(f"Missing version receipt: {receipt_path}")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1
                    or receipt.get("complete") is not True
                    or receipt.get("geant4_version") != manifest["geant4_version"]
                    or receipt.get("manifest_sha256") != manifest_hash):
                raise InstallError(f"Incomplete or mismatched version receipt: {receipt_path}")
            recorded = {item["name"]: item["receipt_sha256"] for item in receipt.get("datasets", [])}
            verified = {item["name"]: item["receipt_sha256"] for item in report["datasets"]}
            recorded_environment = receipt.get("environment_relative")
            if recorded_environment is None:
                # Receipts written before relative paths were added remain
                # valid after mounting the data root into a container.
                recorded_environment = {key: Path(value).name
                                        for key, value in receipt.get("environment", {}).items()}
            if recorded != verified or recorded_environment != relative_environment:
                raise InstallError(f"Version receipt content mismatch: {receipt_path}")
    report["status"] = "ok"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path,
                        default=Path.home() / "geant4-data" / "11.4.2")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--report", type=Path, help="Write an atomic JSON result, including failures")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--base-url", help="Override archive mirror; pinned MD5 checks still apply")
    args = parser.parse_args(argv)
    report = {"schema_version": 1, "status": "failed", "started_at": now(),
              "verify_only": args.verify_only}
    code = 0
    try:
        install(args, report)
    except (InstallError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        report["error"] = str(exc)
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        code = 1
    except KeyboardInterrupt:
        report["error"] = "Interrupted; accepted archives and dataset directories remain recoverable"
        print(report["error"], file=sys.stderr, flush=True)
        code = 130
    report["finished_at"] = now()
    if args.report:
        atomic_json(args.report.expanduser().resolve(), report)
    if code == 0:
        print(f"Geant4 {report['geant4_version']} datasets {'verified' if args.verify_only else 'ready'}: {report['root']}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
