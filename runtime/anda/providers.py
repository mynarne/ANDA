"""Additional official CLI adapter; version/host acceptance remains explicit."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from .core import AndaError, checked_text

def claude_answer(prompt, home, timeout=120):
    executable = shutil.which("claude")
    if not executable:
        raise AndaError("Claude Code executable is not installed")
    env = {k: v for k, v in os.environ.items() if not (k.startswith("ANDA_") and (k.endswith("_TOKEN") or k.endswith("_KEY")))}
    with tempfile.TemporaryDirectory(prefix="provider-", dir=home) as work:
        empty = Path(work) / "mcp.json"
        empty.write_text('{"mcpServers":{}}', encoding="utf-8")
        try:
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostic:
                result = subprocess.run(
                    [executable, "-p", "--safe-mode", "--tools", "", "--disallowedTools", "mcp__*",
                     "--strict-mcp-config", "--mcp-config", str(empty), "--setting-sources", "",
                     "--no-chrome", "--permission-mode", "plan", "--no-session-persistence", "--output-format", "text"],
                    input=prompt.encode("utf-8"), stdout=output, stderr=diagnostic,
                    cwd=work, env=env, timeout=timeout, check=False)
                if result.returncode != 0:
                    raise AndaError("Claude Code exited unsuccessfully; verify official sign-in and supported flags")
                output.seek(0)
                answer = output.read(32001)
                if len(answer) > 32000:
                    raise AndaError("Provider result too large")
            return checked_text(answer.decode("utf-8"))
        except subprocess.TimeoutExpired:
            raise AndaError("Provider timed out; execution/billing outcome is unknown") from None
