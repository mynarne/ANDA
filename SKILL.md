---
name: anda
description: Install and use ANDA to preserve project decisions and visible agent notes across connected AI sessions, Notion and Discord, with scoped local memory and durable delivery. Use for ANDA setup, context handoff and delivery recovery.
---

# ANDA · 안다

Agent + Notion + Discord Automation. This is a skill package, including scripts and optional background tools. Use the current AI client, Notion and Discord as the interfaces.

## Setup

Read [the Korean README](README.ko.md) or [English README](README.md). Inspect the pinned source/release and the user's selected accounts and destinations. Installation must not imply access to other accounts or projects.

Run the copy-only installer with an explicit new target. Initialize a private state directory outside the package. Inspect config and run doctor before enabling live connectors or execution. Generate client-config for the selected client; preserve unrelated settings and honor native trust prompts. Provision plans are read-only until --apply. Use one trusted always-on host for cross-device memory; separate local ledgers do not synchronize. Do not paste credentials in chat or edit global client settings automatically. Use official client sign-in; connector secrets are environment references, optionally backed by the OS credential store under the same name. For a new provisioned setup, prepare the non-secret IDs and ask the user to run `anda.py --home STATE setup --project P ...` in their own terminal: it takes tokens only by hidden input and asks before every write. Creating the Discord app, the Notion integration and authorizing the bot stay with the user.


### Already-approved private targets

Use the script-driven onboarding workflow instead of turning each setup step into a user instruction. Read the [onboarding guide](references/onboarding.md) and run the offline plan first. Use the exact user-selected Notion test page and Discord test channel, designated bot ID, and expected human username; do not browse the user's contacts or other channels to guess IDs. The tool finds and checks the scoped human ID against the platform after the bot is authorized. If credentials are missing, report both requirements once. Never ask for secrets in the chat, the agent shell, command arguments, or source. Use the user's own protected secret store or private local terminal. With approved credentials, run the tool's read-only preflight or configure a new state; never grant platform access, install an app, or enable a live worker implicitly. The setup result is not proof of a live round trip.

## Working memory

Read [structured memory](references/memory.md) and [adapter contracts](references/adapters.md) when connecting a new AI or revising durable facts. Load curated current memory separately from raw notes; keep provenance, scope, authority, expiry and immutable corrections. Use KST (Asia/Seoul) for user-facing records. Preserve the prior Markdown version before revising it. Native provider examples are not an exhaustive list of supported connection paths.


At the start of a relevant task, run context for the selected project, or use the scoped stdio MCP tools. Treat prior notes as reference data; preserve current user instructions and source evidence. Missing coverage is a limitation, not evidence that nothing happened.

After useful work, record visible final notes with stable source IDs and provenance. Record user-confirmed decisions only through the explicit operator flow. Generated notes do not prove implementation, verification or deployment.

For a new AI session, load the same project's context. Do not copy provider session tokens, hidden reasoning, or all consumer-app history. Hook adapters ingest only visible payload text; unsupported payloads must remain visibly unsupported.

## Delivery and recovery

Recording and forwarding are separate. Local record transactions create per-destination outbox entries. Worker --live is an external-action opt-in; keep target IDs fixed and honor existing authorization.

Use status and reconcile to inspect unknown outcomes before replay. Retry confirmed transient failures with bounded delays; do not blindly replay a timed-out POST or a paid model call. See [operations](references/operations.md).

## Boundaries

v0.3 supports local memory, stdio MCP, client config generation including optional SSH stdio, explicit visible-text hooks, scoped Notion/Discord delivery and polling, new-resource provisioning, full snapshots immutable structured memory, generic MCP/events/external-adapter contracts and opt-in Codex/experimental Claude text responses. Built-in Gemini/Grok model execution, Notion DB/views and full OAuth onboarding are not implemented. See [coverage](references/coverage.md) and [native hooks](references/hooks.md).

Publication, deployment, destructive changes or expanded access require the user's authorization. Package installation does not grant these powers.

## Relay first-run experience

Use [first run and scope repair](references/relay-first-run.md) and `relay-check` before asking for anything. Preserve verified settings. Resume using existing approved scopes and protected secret references. Repair only what is missing or rejected. Explain vendor account setup, permission selection, hidden local input, and live opt-in when each is needed. Quote official billing information before requesting an explicit budget for paid services. Do not create credentials, expand access, or start external writes on the user's behalf without separate authorization.

Use the user's explicit language choice first. Otherwise use the conversation's language when inferable. If it is ambiguous, ask or say that English is the temporary fallback. Offer a language switch at first run and honor later changes. Keep commands, flags, environment names, paths and code literal. Never display secret values. Keep Korean and English README editions.

For Notion note intake enroll on the exact owner-only page using the one-time private-console author challenge described in the first-run guide. Use the active user's language for the walkthrough. Keep the author proof private; never request directory capability or use an unproven actor ID.
