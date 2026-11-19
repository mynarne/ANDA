# Coverage / 지원 범위

## v0.3 additions · KST display

Structured project memory has immutable sourced revisions, current/proposal separation, expiry/retraction, optimistic conflict checks and local-only exclusions. Notion memory routing is implemented but live runtime credentials remain absent. Generic MCP labels, visible-text events and external subprocess v1 let additional AI tools integrate; only the transport/local paths are tested. No blanket consumer-app history or all-provider inference claim. See memory.md, adapters.md and VERIFICATION-0.3.md.

The v0.2 matrix below records the inherited baseline; the external generic execution interface is now additionally available.


## v0.2.0

| Path | Implemented | Observed status |
| --- | --- | --- |
| Local record/context/export | Project scope, stable IDs, bounded excerpts | Real CLI subprocess |
| Full snapshot | SQLite backup API, integrity check, private permissions | Real temporary database with 120 events |
| MCP handoff | Read context / write assistant notes, fixed project and configured client label | Real independent subprocess sessions; not native AI model inference |
| Client configuration | Codex/Grok TOML, Claude/Gemini JSON | Parsed and scope-tested; native UI registration pending |
| SSH stdio | One host/ledger, strict host keys, quoted remote command | Configuration tests only; no actual remote host connection |
| Visible-text hooks | Codex / Claude / Gemini / Grok normalization | Synthetic official-shaped payloads |
| Notion output/intake | Fixed parent pages, direct paragraphs, allowlisted authors, baseline | Simulated REST; runtime token absent |
| Discord output/intake | Fixed channel, bot identity, nonce/marker, allowlisted commands | Simulated REST; runtime bot token absent |
| New workspace provisioning | Four Notion pages, Discord private category/workroom, creation journal | Simulated REST; live permissions and receipts pending |
| Model start budget | All projects share UTC daily attempt cap | Local concurrency/transaction logic; not a money/token cap |
| Codex execution | Official ephemeral CLI, scoped state/log paths, read-only sandbox, selected tools disabled | Real diagnostic failed at host runtime initialization; no final answer |
| Claude execution | Official print-mode stdin adapter, selected tools/customizations disabled | Experimental; subprocess invocation simulated, CLI absent |
| Gemini/Grok execution | Not implemented | Memory through MCP/hooks only |
| Consumer apps' whole histories | Not implemented | No account-wide automatic capture |
| OAuth, Notion DB/views, Discord role/server creation | Not implemented | User creates/authorizes the integration and bot |
| OS supervisor, retention/deletion, large-queue controls | Not implemented | Foreground worker; target-device/soak acceptance pending |

Native hook configuration is explicit. SessionStart/Input can return supported additionalContext shapes with --native. Grok passive hook stdout cannot inject memory; use MCP. Unsupported final-text payloads are skipped; transcript_path and hidden reasoning are never read.

Each source needs stable event IDs for source-level deduplication. Generated identities are disclosed and cannot deduplicate repeated delivery of the same hook.

Notion scans at most 1,000 direct blocks. Discord scans at most 1,000 messages back to its cursor. Exceeded bounds fail without silently moving the cursor or claiming complete coverage. Deleted/unavailable messages and offline edits cannot be reconstructed. Polling is not instant real time.

The runtime trusts one OS user. Native clients must use the same host/ledger to share memory; separate local databases are not automatically merged. SSH is a configuration option, not a verified deployment. Public multi-tenant hosting is outside scope.

## Primary references

- [Codex non-interactive](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [Claude CLI](https://code.claude.com/docs/en/cli-reference)
- [Claude hooks](https://code.claude.com/docs/en/hooks)
- [Gemini MCP](https://geminicli.com/docs/tools/mcp-server/)
- [Gemini hooks](https://geminicli.com/docs/hooks/reference/)
- [Grok MCP and project TOML](https://docs.x.ai/build/features/mcp-servers)
- [Grok hooks](https://docs.x.ai/build/features/hooks)
- [MCP stdio](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [Notion create page](https://developers.notion.com/reference/post-page)
- [Notion limits](https://developers.notion.com/reference/request-limits)
- [Discord guild channels](https://docs.discord.com/developers/resources/guild)
- [Discord messages](https://docs.discord.com/developers/resources/message)
- [Discord rate limits](https://docs.discord.com/developers/topics/rate-limits)

See [relay verification](../VERIFICATION-RELAY.md) for newer Windows live results. The tables above are the historical baseline.
