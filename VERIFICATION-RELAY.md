# ANDA relay verification

## Windows private live acceptance · 2026-11-18 KST

The fixed private test page and channel were used with non-personal synthetic inputs. During one foreground run, all three routes were enabled together:

| Input | Independently read back |
| --- | --- |
| One allowlisted plain Discord message without the command prefix | One Notion request archive and one actual signed-in Codex response in Discord and Notion |
| One direct Notion paragraph from the registered author | One Discord mirror without a model job |
| One new official Codex root session with project-local approved UserPromptSubmit and Stop hooks | Two visible events, each read back from Discord and Notion |

Eight distinct external receipts were confirmed. The reopened ledger produced zero duplicate deliveries, and the relay reserved one model start for Discord ingress. The worker stopped and the temporary native hooks were disabled. A separate Notion-only acceptance run also verified the read-only MCP change cursor. Prior one-event Claude/Gemini readback is separate from native hook verification.

No private test identifiers, original transcripts or credentials are included. The one-shot Windows orchestration used for acceptance remains host-local. The repository provides portable worker and hook entry points, tested independently. It does not include an OS boot service or webhooks receiver. Unattended, cross-platform and long-duration operation remain unverified.

## Reproduce offline

Run `python -B -m unittest discover -s tests -q` and `python -B scripts/check_release.py`.
