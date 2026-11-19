"""Test one real Codex response through ANDA without Discord or Notion credentials."""
import json
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from anda.core import AndaError
from anda.engine import codex_answer

if __name__ == "__main__":
    marker = "ANDA-LIVE-" + uuid.uuid4().hex[:12]
    try:
        with tempfile.TemporaryDirectory(prefix="anda-model-probe-") as root:
            result = codex_answer("Return only " + marker + ".", Path(root).resolve(), timeout=60)
        if result.strip() != marker:
            raise AndaError("The native model did not return the expected synthetic result")
    except (OSError, AndaError) as error:
        print(json.dumps({"state": "codex_not_verified", "reason": str(error) if isinstance(error, AndaError) else "OS runtime failed"}))
        raise SystemExit(2)
    print(json.dumps({"state": "codex_probe_passed", "external_deliveries": 0, "full_roundtrip": False}))
