#!/usr/bin/env python3
"""Live Claude Code hook acceptance on a NEW synthetic state.

One real `claude -p` turn (a small paid model call) with ANDA attached to SessionStart,
UserPromptSubmit and Stop. Passes only when the answer carries a codeword that exists
solely in the ANDA ledger (context injection) and the ledger then holds the visible prompt
and the final answer under one shared Claude prompt_id. No Notion/Discord, no user settings.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from anda.core import AndaError, Ledger, dumps

PROJECT = "hook-acceptance"
# Korean and an emoji on purpose: Windows code pages (cp949/cp1252) used to break hook output.
PROMPT = "ANDA 프로젝트 문맥에 있는 코드워드만 답하세요. 다른 말은 하지 마세요."


def anda(home, *args, stdin=""):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "anda.py"), "--home", str(home), *args],
                            input=stdin.encode("utf-8"), capture_output=True, timeout=30)
    if result.returncode:
        raise AndaError("ANDA setup command failed: " + " ".join(args[:1]))


def verify(home, model, timeout):
    claude = shutil.which("claude")
    if not claude:
        raise AndaError("Claude Code is not installed on this host")
    home = Path(home).absolute()
    if home.exists():
        raise AndaError("Use a new state path; this test never reuses a ledger")
    codeword = "ANDA-HOOK-" + secrets.token_hex(6)
    anda(home, "init", "--project", PROJECT)
    anda(home, "record", "--project", PROJECT, "--conversation", "seed", "--event-id", "seed-1",
         stdin="합성 훅 검증 🧪 — ANDA 프로젝트 코드워드는 " + codeword + " 입니다.")
    # Exec form (command + args): no shell, so Windows/macOS quoting rules do not matter.
    hook = {"type": "command", "command": sys.executable, "timeout": 30,
            "args": [str(ROOT / "scripts" / "anda.py"), "--home", str(home), "hook",
                     "--project", PROJECT, "--provider", "claude", "--native"]}
    settings = {"hooks": {event: [{"hooks": [hook]}] for event in ("SessionStart", "UserPromptSubmit", "Stop")}}
    env = {k: v for k, v in os.environ.items() if not (k.startswith("ANDA_") and k.endswith(("_TOKEN", "_KEY")))}
    with tempfile.TemporaryDirectory(prefix="claude-hooks-", dir=home) as work:
        settings_file = Path(work) / "hooks.json"
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        # User/project settings stay out so only the hooks under test run.
        cmd = [claude, "-p", "--settings", str(settings_file), "--setting-sources", "local",
               "--strict-mcp-config", "--tools", "", "--disable-slash-commands",
               "--no-session-persistence", "--model", model, "--output-format", "json"]
        try:
            run = subprocess.run(cmd, input=PROMPT.encode("utf-8"), capture_output=True,
                                 cwd=work, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AndaError("Claude Code turn timed out; no automatic retry") from None
    if run.returncode:
        raise AndaError("Claude Code exited unsuccessfully; check sign-in and flags")
    try:
        final = json.loads(run.stdout.decode("utf-8"))
    except ValueError:
        raise AndaError("Unrecognized Claude Code JSON result") from None
    answer = str(final.get("result", "")).strip()
    ledger = Ledger(home)
    try:
        rows = [dict(r) for r in ledger.db.execute(
            "SELECT conversation,source_id,role,text FROM events WHERE project=? AND source='claude' ORDER BY rowid",
            (PROJECT,))]
    finally:
        ledger.close()
    prompt_rows = [r for r in rows if r["role"] == "user" and r["source_id"].endswith(":UserPromptSubmit")]
    stop_rows = [r for r in rows if r["role"] == "assistant" and ":Stop:" in r["source_id"]]
    checks = {
        "turn_succeeded": final.get("is_error") is False,
        "context_injected": codeword in answer,
        "prompt_recorded": len(prompt_rows) == 1 and prompt_rows[0]["text"].strip() == PROMPT,
        "answer_recorded": len(stop_rows) == 1 and stop_rows[0]["text"].strip() == answer,
        # Separately generated identities would differ; one prefix means Claude's prompt_id was used.
        "shared_prompt_id": bool(prompt_rows and stop_rows) and
                            prompt_rows[0]["source_id"].split(":")[0] == stop_rows[0]["source_id"].split(":")[0],
        "session_scoped": bool(rows) and {r["conversation"] for r in rows} == {final.get("session_id")},
    }
    return {"state": "claude_hooks_observed" if all(checks.values()) else "failed", "checks": checks,
            "model": model, "claude_events": len(rows), "network_calls": "one Claude Code turn"}


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--home", required=True, help="NEW private state directory used only for this test")
    p.add_argument("--model", default="haiku")
    p.add_argument("--timeout", type=int, default=180)
    a = p.parse_args()
    try:
        result = verify(a.home, a.model, a.timeout)
    except (AndaError, OSError) as exc:
        result = {"state": "failed", "message": str(exc) if isinstance(exc, AndaError) else type(exc).__name__}
    print(dumps(result))
    return 0 if result["state"] == "claude_hooks_observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
