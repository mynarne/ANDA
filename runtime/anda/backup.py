"""Consistent full-ledger snapshot. Private data; never package or publish this output."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from .core import AndaError, dumps, unsafe_link

def snapshot(ledger, target):
    target = Path(target).absolute()
    if target.exists() or any(unsafe_link(x) for x in [target, *target.parents]):
        raise AndaError("Backup target must be new and contain no symlink components")
    package = Path(__file__).resolve().parents[2]
    # Compare canonical paths: /var/x and /private/var/x (macOS) name the same folder.
    real, home = target.resolve(strict=False), ledger.home.resolve()
    if real == package or package in real.parents or real == home or home in real.parents:
        raise AndaError("Keep private backups outside the package and active state")
    if not target.parent.is_dir():
        raise AndaError("Backup parent must already exist")
    with tempfile.TemporaryDirectory(prefix=".anda-backup-", dir=target.parent) as temp:
        stage = Path(temp) / "snapshot"
        stage.mkdir(mode=0o700)
        database = stage / "ledger.sqlite3"
        dest = sqlite3.connect(database)
        try:
            ledger.db.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AndaError("Backup integrity check failed")
        finally:
            dest.close()
        (stage / "config.json").write_text(dumps(ledger.config) + "\n", encoding="utf-8")
        files = {name: hashlib.sha256((stage / name).read_bytes()).hexdigest() for name in ["ledger.sqlite3", "config.json"]}
        (stage / "SNAPSHOT.json").write_text(dumps({"version": 1, "at": ledger.clock(), "files": files, "private": True}) + "\n", encoding="utf-8")
        if os.name != "nt":
            for path in stage.iterdir():
                path.chmod(0o600)
        if target.exists():
            raise AndaError("Backup target appeared during copy")
        stage.rename(target)
    return {"state": "snapshot_created", "path": str(target), "integrity_check": "ok", "private": True,
            "note": "Full SQLite snapshot including outbox; config contains environment references, not secret values."}
