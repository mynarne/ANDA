# Structured memory / 지속 메모리

Version 0.3.0. User-facing timestamps use KST (Asia/Seoul, UTC+9); internal instants remain UTC epoch seconds.

Raw events preserve what was said. Structured memory separately tracks **decision, preference, progress and context** by project and stable key. Each revision includes its source event, scope, recorded/effective/expiry times, authority, status and previous revision. A source from another project is rejected.

## Store, search, correct

Record the explicit source first and retain its returned event ID. Then:

~~~sh
python3 scripts/anda.py --home /absolute/private/state memory-put \
  --project myproject --kind preference --key writing-language \
  --source-event SOURCE_EVENT_ID --operation-id preference-001 \
  --confirmed-by-user --file preference.txt
python3 scripts/anda.py --home /absolute/private/state memory-list --project myproject
python3 scripts/anda.py --home /absolute/private/state memory-history \
  --project myproject --kind preference --key writing-language
~~~

Corrections use a new operation ID plus --expected-revision CURRENT_REVISION. A stale writer must reload the current value. Retractions need --status retracted and explicit --confirmed-by-user, preserve old rows and require a reason as text. Future effective times are rejected; optional --expires-at/--effective-at take Unix epoch seconds. Expired/retracted entries are excluded from default current context.

Repeated identical operation IDs are idempotent. Different content under the same ID is rejected. Revision rows have SQLite triggers preventing UPDATE and DELETE. This preserves corrections inside the application, but an OS owner can still replace the database; it is not tamper-proof storage.

## Agent authority and privacy

MCP memory_put can propose decisions/preferences. Only an explicit local operator confirmation activates them. Agent progress/context can be current but is labeled agent_reported, never independently verified. Later confirmed corrections close older proposals. Historical raw notes remain history; curated current memory takes precedence when interpreting prior facts, and none of it grants new execution permission.

MCP memory_lookup returns current records and separate proposals. memory_history shows superseded revisions. memory_context includes up to 6,000 UTF-8 text bytes of curated memory with provenance. Queries/history are bounded to protect the local process; a bounded result is not a complete archive.

An operator can select --local-only. That revision has no forwarding mirror and is excluded from MCP, provider context and ordinary memory history. Local operator --include-local can inspect it. This does not undo prior source-event delivery; avoid putting sensitive source text in a forwarded event in the first place. Backups still contain all local memory and need private storage.

## Notion memory tab

provision creates a separate Memory page and saves memory_page_id in private config. Structured revisions create separate append-only child pages there, while ordinary conversation records continue under Releases. Each queued record freezes the selected target; later configuration changes never reroute private backlog.

Notion is a revision archive, not a live SQL view. An old archive page's active status describes that revision at write time. Current/expired/superseded decisions come from the ledger lookup. Manual Notion edits do not silently rewrite confirmed memory. Automatic import/merge of arbitrary Notion edits is not implemented.

