"""Observe a native Claude Code reading one synthetic result from ANDA."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from anda.core import AndaError

TOOL = "mcp__anda__memory_context"


def matching_context(payload, marker, event_id):
    if isinstance(payload, str):
        payload = [{"type": "text", "text": payload}]
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            context = json.loads(item.get("text", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(context, dict) and any(
            event.get("id") == event_id and isinstance(event.get("text"), str) and event["text"].strip() == marker
            for event in context.get("events", []) if isinstance(event, dict)
        ):
            return True
    return False


def confirm_trace(trace, marker, event_id):
    calls, matches, final = set(), set(), []
    for entry in trace:
        if entry.get("parent_tool_use_id"):
            continue
        if entry.get("type") == "result":
            final.append(entry)
        content = entry.get("message", {}).get("content", [])
        for block in content if isinstance(content, list) else []:
            kind = block.get("type")
            if entry.get("type") == "assistant" and kind == "tool_use":
                if block.get("name") != TOOL:
                    raise AndaError("Unexpected native Claude tool call")
                if block.get("input") == {"query": marker}:
                    calls.add(block.get("id"))
            if entry.get("type") == "user" and kind == "tool_result":
                ident = block.get("tool_use_id")
                if ident and ident in calls and not block.get("is_error") and matching_context(block.get("content"), marker, event_id):
                    matches.add(ident)
    if not matches or len(final) != 1 or final[0].get("subtype") != "success" or final[0].get("is_error") or str(final[0].get("result", "")).strip() != event_id:
        raise AndaError("No matching native Claude tool result and final response were observed")
    return {"state": "claude_mcp_readback_observed", "response_event_id": event_id}


def readback(root, home, project, marker, event_id):
    executable = shutil.which("claude")
    if not executable:
        raise AndaError("Claude Code is not installed on this host")
    keep = {"PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "LANG", "LC_ALL",
            "TERM", "TMPDIR", "TMP", "TEMP", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
            "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    prompt = ("Use ANDA memory_context with query " + marker
              + ". Return only the ID of the assistant response event whose text is the marker.")
    with tempfile.TemporaryDirectory(prefix="claude-readback-", dir=home) as directory:
        work = Path(directory)
        config = work / "mcp.json"
        args = [str(root / "scripts" / "anda.py"), "--home", str(home), "mcp", "--project", project,
                "--agent", "claude-acceptance", "--probe-marker", marker, "--probe-event-id", event_id]
        config.write_text(json.dumps({"mcpServers": {"anda": {"command": sys.executable, "args": args}}}), encoding="utf-8")
        cmd = [executable, "-p", "--restricted", "--strict-mcp-config", "--mcp-config", str(config),
               "--tools", "", "--allowedTools", TOOL, "--permission-prompts", "none",
               "--no-chrome", "--no-session-persistence", "--disable-slash-commands",
               "--max-turns", "3", "--output-format", "stream-json", "--verbose"]
        try:
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(cmd, input=prompt.encode(), stdout=output, stderr=subprocess.DEVNULL,
                                        env=env, cwd=work, timeout=90, check=False)
                if result.returncode:
                    raise AndaError("Native Claude MCP test did not complete")
                output.seek(0)
                data = output.read(1_000_001)
        except subprocess.TimeoutExpired:
            raise AndaError("Native Claude MCP test timed out; no automatic retry") from None
    if len(data) > 1_000_000:
        raise AndaError("Native Claude trace exceeded its verification bound")
    try:
        return confirm_trace([json.loads(line) for line in data.splitlines() if line.strip()], marker, event_id)
    except (ValueError, AttributeError, TypeError):
        raise AndaError("Unrecognized native Claude event stream") from None
