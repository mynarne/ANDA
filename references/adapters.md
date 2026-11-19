# Adapter contract / 범용 AI 연동

ANDA is extensible beyond the bundled provider examples. A platform's actual authorization and interfaces determine whether a particular connection works; a universal connector cannot expose every consumer application's chats.

## Supported connection paths

| Path | Contract | Limits |
| --- | --- | --- |
| MCP-capable AI client | Project-bound stdio tools, optional SSH stdio to one trusted host | Native client registration and trust remain necessary |
| Tool-capable agent without MCP | Explicit CLI record/context/memory commands | The agent must invoke them or an authorized hook |
| Visible-text hooks/export | anda.event.v1 JSON on stdin | Stable IDs, no hidden reasoning or account-wide scraping |
| External model/CLI/API wrapper | anda.agent.request.v1 → anda.agent.response.v1 | Reviewed local executable; official provider access; opt-in execution |
| Consumer app without a supported integration | User-provided export or manual handoff | No automatic private history claim |

## Generic MCP configuration

~~~sh
python3 scripts/anda.py --home /absolute/private/state client-config \
  --project myproject --client generic --agent-label your-ai
~~~

This prints a common JSON mcpServers entry. Translate it to the client's documented format if necessary. --client codex/grok emits their native TOML; claude/gemini emit JSON. Labels are configured provenance, not verified identity. The server accepts bounded lowercase labels for any client.

## Visible event v1

Send a JSON object on stdin to hook --provider your-ai --project myproject:

~~~json
{
  "schema": "anda.event.v1",
  "event_type": "assistant_message",
  "conversation_id": "task-42",
  "event_id": "turn-17-visible-final",
  "text": "Visible final note with evidence."
}
~~~

event_type is session_start, user_prompt or assistant_message. session_start returns scoped context. Text events require a stable event_id. An optional project must match the CLI-bound project. Unknown native injection formats are not guessed; read the generic context or use MCP.

## External response adapter v1

Private provider configuration can select an explicit reviewed executable:

~~~json
{
  "name": "external",
  "label": "your-ai",
  "enabled": false,
  "timeout_seconds": 120,
  "max_calls_per_day": 20,
  "argv": ["/absolute/python3", "/absolute/reviewed/provider_adapter.py"],
  "pass_env": ["YOUR_PROVIDER_API_KEY"]
}
~~~

This is an execution interface, not a bundled working backend. The operator reviews/pins that adapter and supplies only its authorized environment names. Do not put credentials in argv/config or bypass provider login/terms. ANDA_* connector variables cannot be passed through this interface.

The program receives one JSON stdin request:

~~~json
{"schema":"anda.agent.request.v1","prompt":"Scoped context plus current request","constraints":{"text_response_only":true,"verification_required":true}}
~~~

Return exactly one JSON response to stdout; diagnostics belong on stderr:

~~~json
{"schema":"anda.agent.response.v1","answer":"Visible final answer with evidence","provider_label":"your-ai"}
~~~

The response limit is 64,000 bytes; accepted answer text is at most 32,000 UTF-8 bytes. Unknown fields/formats are rejected. No shell is used. Prompt text is not in argv. Environment is limited to basic OS/client-login paths and explicitly selected names. Timeouts/failures stay unresolved without paid automatic replay.

The label/argv/env selection is fingerprinted into the queued job provider identity. Changing it cannot silently reroute old requests. The adapter is trusted operator-selected code; ANDA does not sandbox arbitrary third-party programs or verify their semantic claims.

The generic subprocess protocol has a real local roundtrip test using a deterministic helper. That is transport verification, **not real inference by every AI service**. Built-in Codex inference is blocked by the current host initialization; Claude is experimental/uninstalled here. Gemini/Grok and other services can use MCP/events or supply a separately tested official wrapper.

## Add an adapter

1. Use the provider's supported CLI/API and official account sign-in.
2. Implement stdin/output v1 without hidden reasoning/session-token extraction.
3. Test malformed output, secret redaction, timeout ambiguity, duplicate IDs and changed backend fingerprint.
4. Run real end-to-end acceptance on an authorized host and record provider version, actual result and platform receipt IDs.
5. Update the support matrix only to the verified scope. Never label a generic schema as all-service live support.
