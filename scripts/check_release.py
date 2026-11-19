#!/usr/bin/env python3
"""Offline public-package gate. Does not read files outside the package."""
import hashlib
import json
from pathlib import Path
import re
import sys

root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/"MANIFEST.json").read_text(encoding="utf-8"))
problems=[]
patterns={
    "private_key":re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider_token":re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|ntn_[A-Za-z0-9]{20,})\b"),
    "personal_email":re.compile(r"\b[A-Za-z0-9_.+-]+@(?:gmail\.com|naver\.com|icloud\.com|outlook\.com)\b",re.I),
}
# One exact, intentionally synthetic redaction fixture. No path-wide exclusion.
synthetic={("tests/test_anda.py","sk-"+"secretvaluefortest12345")}
for name,digest in manifest["files"].items():
    rel=Path(name)
    path=root/rel
    if rel.is_absolute() or ".." in rel.parts or any(p.is_symlink() for p in [path,*path.parents]):
        problems.append({"path":name,"problem":"unsafe path"})
        continue
    if path.name=="config.json" or path.name.startswith(".env") or path.suffix in {".sqlite3",".sqlite",".db",".log",".zip"} or ".git" in rel.parts:
        problems.append({"path":name,"problem":"private/runtime file type"})
        continue
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:
        problems.append({"path":name,"problem":"hash mismatch"})
    if name=="assets/anda-icon-v01.png":
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(raw).hexdigest()!="92b0a2c231e2ba8f4fe8ce774dde7eec7f8514065674722a93b23b83e56cd891":
            problems.append({"path":name,"problem":"invalid approved icon"})
        continue
    try:
        text=raw.decode("utf-8")
    except UnicodeError:
        problems.append({"path":name,"problem":"unexpected binary"})
        continue
    for label,pattern in patterns.items():
        for match in pattern.finditer(text):
            if (name,match.group()) not in synthetic:
                problems.append({"path":name,"problem":label})
result={"state":"passed" if not problems else "failed","version":manifest["version"],
        "manifest_files":len(manifest["files"]),"problems":problems,
        "scope":"Limited offline review, not a comprehensive security audit or publisher signature"}
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(1 if problems else 0)
