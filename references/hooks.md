# Native hook wiring / 네이티브 훅 연결

The installer never edits client configuration. The agent may apply a reviewed project-local hook config only within the user's authorized setup scope. Keep the exact command definition visible for client trust review. Use a pinned package path, a private state path and a configured project.

## Codex / Claude Code

Example command (replace paths; correctly quote spaces for your shell):

~~~text
python3 /absolute/skills/anda/scripts/anda.py --home /absolute/private/anda-state hook --project myproject --provider claude --native
~~~

For Claude Code, prefer the exec form so no shell quoting applies on any OS: `{"type": "command", "command": "/absolute/python", "args": ["/absolute/skills/anda/scripts/anda.py", "--home", "/absolute/private/anda-state", "hook", "--project", "myproject", "--provider", "claude", "--native"]}`. `scripts/verify_claude_hooks.py --home <new path>` runs one real Claude Code turn (a small paid call) against a new synthetic state with only these hooks loaded, and passes when the injected context, the recorded prompt/answer and the shared `prompt_id` all check out.

Use provider codex for Codex. Attach this command to SessionStart, UserPromptSubmit and Stop through each client's supported settings. SessionStart/UserPromptSubmit return hookSpecificOutput.additionalContext with hookEventName. Stop stores only last_assistant_message when explicitly provided. A Stop payload without final visible text is not archived. Claude Code has no turn_id; ANDA uses its per-prompt `prompt_id` (Claude Code v2.1.196+) as the stable identity, so no --event-id is needed. Stop identities also carry a digest of the final text, so a prompt that ends more than once records each distinct answer once.

## Gemini CLI

Attach the same command with --provider gemini --native to SessionStart, BeforeAgent and AfterAgent. Startup/input returns hookSpecificOutput.additionalContext; AfterAgent stores prompt_response. The official CLI handles its own Google login. ANDA does not extract OAuth credentials or call the Code Assist backend directly.

## Grok Build

Use the scoped MCP tools to load and save memory. Grok passive hook stdout is ignored, so ANDA's --native path does not claim to inject context there. Prompt hooks can record an explicit prompt payload if present; final-text availability depends on the actual payload. Unsupported input remains unsupported.

## Reliability

Provide a stable --event-id when the host integration has one. Native turn_id, event_id, timestamp or Claude's prompt_id is used when present. Otherwise a generated identity is disclosed on stderr; source-level duplicate delivery cannot be guaranteed.

Hook envelopes up to 1 MiB are accepted. Native hook text (not `anda.event.v1` envelopes, which must stay within 32,000 bytes) beyond the 32,000-byte ledger limit is redacted, then stored as a marked excerpt, and context injection still runs; relay routes skip such text instead. Hook and MCP output is UTF-8 on every platform, including Windows code pages such as cp949.

A native hook storage failure emits a non-blocking warning and does not falsely claim it archived the turn. The regular envelope command returns an error for explicit operator use. Validate your client/version and simulated payload shape before enabling the hook. Full native client deployment and cross-platform acceptance are still pending.
