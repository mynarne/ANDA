#!/usr/bin/env python3
"""Manifest-verified copy-only install; no global config or background process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

p=argparse.ArgumentParser(description="Install the reviewed ANDA package to a new explicit target")
p.add_argument("--target",required=True)
p.add_argument("--apply",action="store_true")
a=p.parse_args()
source=Path(__file__).resolve().parents[1]
target=Path(a.target).expanduser().absolute()
# Same rule as anda.core.unsafe_link: root-owned OS aliases (macOS /var, /tmp) are allowed.
unsafe=lambda x:x.is_symlink() and (os.name=="nt" or x.lstat().st_uid!=0)
if target==source or source in target.parents or target.exists() or any(unsafe(x) for x in [target,*target.parents]):
    p.error("Use a new target outside the source with no symlink components")
try:
    manifest=json.loads((source/"MANIFEST.json").read_text(encoding="utf-8"))
    for name,digest in manifest["files"].items():
        rel=Path(name)
        if rel.is_absolute() or ".." in rel.parts or any(unsafe(x) for x in [source/rel,*(source/rel).parents]):
            raise ValueError("unsafe package entry")
        if hashlib.sha256((source/rel).read_bytes()).hexdigest()!=digest:
            raise ValueError("package hash mismatch")
except (OSError,ValueError,KeyError,TypeError):
    p.error("Package manifest validation failed; obtain a complete reviewed release")
if not a.apply:
    print("PLAN: copy "+str(len(manifest["files"]))+" checked files to "+str(target)+"; no service, hooks, secrets or global settings changed")
else:
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".anda-stage-",dir=target.parent) as staging:
        stage=Path(staging)/"anda";stage.mkdir()
        for name in [*manifest["files"],"MANIFEST.json"]:
            dest=stage/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/name,dest)
        # Fail if the final target appeared after validation.
        if target.exists():p.error("Target appeared during installation")
        stage.rename(target)
    print("Installed ANDA skill package: "+str(target))
    print("Read SKILL.md; initialize private state and run doctor. No background process started.")
