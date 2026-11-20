#!/usr/bin/env python3
"""Real local CLI/MCP roundtrip. No network, credentials, providers or fake API service."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="anda-smoke-") as tmp:
    state=Path(tmp)/"private-state"
    base=[sys.executable,str(root/"scripts/anda.py"),"--home",str(state)]
    def run(args,stdin=""):
        result=subprocess.run(base+args,input=stdin,text=True, encoding="utf-8",capture_output=True,timeout=15)
        if result.returncode:
            raise RuntimeError("Local subprocess failed: "+result.stderr)
        return result.stdout
    run(["init","--project","example"])
    record={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
        "name":"memory_record","arguments":{"conversation":"local-client-a","event_id":"note-1",
        "text":"Local acceptance note: retain provenance when continuing a task."}}}
    def mcp(message, agent="codex"):
        init={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
        output=run(["mcp","--agent",agent,"--project","example"],json.dumps(init)+"\n"+json.dumps(message)+"\n")
        return json.loads(output.splitlines()[-1])
    first=mcp(record)
    repeated=mcp(record)
    context=mcp({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"memory_context","arguments":{}}},agent="claude")
    stored=json.loads(context["result"]["content"][0]["text"])
    duplicate=json.loads(repeated["result"]["content"][0]["text"])
    exported=run(["export","--project","example"])
    assert len(stored["events"])==1 and duplicate["duplicate"]
    assert "retain provenance" in exported
    assert stored["events"][0]["source"]=="mcp:codex"
    print(json.dumps({
        "mode":"real-local-integration-no-external-api",
        "mcp_clients":"two independent subprocess sessions with codex/claude configuration labels (not native AI clients)",
        "shared_memory_roundtrip":True,"duplicate_record_count":len(stored["events"]),
        "markdown_export":True,"external_api_calls":0,"provider_inference_calls":0,
        "native_AI_client_installation":"not_tested"
    },indent=2))
