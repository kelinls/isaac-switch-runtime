#!/usr/bin/env python3
import argparse
import ftplib
import hashlib
import json
import shutil
import tempfile
from pathlib import Path


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _remote_bytes(ftp, path):
    chunks = []
    ftp.retrbinary(f"RETR {path}", chunks.append)
    return b"".join(chunks)


def _ensure_remote_directory(ftp, path: str):
    """Create every missing parent directory of a remote file path.

    The device FTP server does not create parents for ``STOR``: uploading the first file of a
    brand new Mod directory fails with ``553 No such file or directory``. ``MKD`` on a directory
    that already exists is an error too, so failures are ignored here and the following ``STOR``
    stays the authoritative check.
    """
    parts = path.split("/")[:-1]
    for index in range(1, len(parts) + 1):
        prefix = "/".join(parts[:index])
        try:
            ftp.mkd(prefix)
        except Exception:
            continue


def verify_package(ftp, root: Path, manifest: dict, readback: Path):
    uploaded = []
    verified = []
    readback.mkdir(parents=True, exist_ok=True)
    for entry in manifest.get("files", []):
        relative = entry["path"]
        local = root / relative
        expected = local.read_bytes()
        if len(expected) != entry["size"] or _hash(expected) != entry["sha256"]:
            raise ValueError(f"local manifest mismatch: {relative}")
        try:
            remote = _remote_bytes(ftp, relative)
        except Exception:
            remote = None
        if remote != expected:
            _ensure_remote_directory(ftp, relative)
            with local.open("rb") as source:
                ftp.storbinary(f"STOR {relative}", source)
            uploaded.append(relative)
        restored = _remote_bytes(ftp, relative)
        target = readback / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(restored)
        if len(restored) != entry["size"] or _hash(restored) != entry["sha256"]:
            raise ValueError(f"remote readback mismatch: {relative}")
        verified.append(relative)
    manifest_path = root / "test-build-manifest.json"
    if manifest_path.exists():
        expected = manifest_path.read_bytes()
        relative = "test-build-manifest.json"
        try:
            remote = _remote_bytes(ftp, relative)
        except Exception:
            remote = None
        if remote != expected:
            with manifest_path.open("rb") as source:
                ftp.storbinary(f"STOR {relative}", source)
            uploaded.append(relative)
        restored = _remote_bytes(ftp, relative)
        target = readback / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(restored)
        if restored != expected:
            raise ValueError(f"remote readback mismatch: {relative}")
        verified.append(relative)
    return {"uploaded": uploaded, "verified": verified}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=21)
    parser.add_argument("--user", default="anonymous")
    parser.add_argument("--password", default="anonymous@")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--readback", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    ftp = ftplib.FTP()
    ftp.connect(args.host, args.port)
    ftp.login(args.user, args.password)
    try:
        print(json.dumps(verify_package(ftp, args.root, manifest, args.readback), indent=2))
    finally:
        ftp.quit()


if __name__ == "__main__":
    main()
