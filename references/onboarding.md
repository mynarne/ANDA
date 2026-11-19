# Script-driven onboarding for approved existing endpoints

For a private Notion page and a private Discord text channel that the operator
already selected and authorized, run:

    python3 scripts/onboard.py --home /absolute/new/anda-state --project demo --notion-page PAGE_LINK --discord-channel CHANNEL_LINK --discord-bot BOT_ID --discord-owner ACCOUNT_USERNAME

The same form accepts explicit target IDs. The expected owner can be an exact human
Discord username instead of a numeric user ID. ANDA verifies the unique allowed
channel member or, if no member overwrite exists, the designated server owner.
It never searches accounts or contacts; a username mismatch stops setup.

- With no flags: offline plan and missing **environment variable names** only.
  No API calls or state creation.
- With --check: authorized GET calls only. Confirms Notion connection identity
  and exact non-public page, designated Discord bot, exact channel and private
  permission rules. No local writes or provider calls.
- With --apply: same checks, then writes a new local ANDA ledger and config.
  No external writes, apps installed, accounts logged in, or permissions granted.
  An existing state directory is never reused. --enable-codex configures the
  installed provider but does not call it; the separate live worker requires
  its own opt-in. Provider stays off by default.
- Credentials come from the protected ANDA_NOTION_TOKEN / ANDA_DISCORD_TOKEN
  runtime environment or, on the owner's personal terminal only, --prompt-secrets
  for this setup process. A later worker must receive its own runtime credentials;
  setup never saves them to disk or passes them to an AI.
- User authentication, application installation and resource access grants still
  use the official services with the owner or administrator's approval. Prefer
  read/insert-only Notion access confined to the test page and the test bot's
  explicit View Channel, Send Messages, and Read Message History here.
  Discord server owners and administrators can bypass channel restrictions.

A successful read check is not proof of Notion insert permission, Discord
delivery, a model response, or a live round trip. Prove those separately using
original input and actual delivery receipt IDs.

Use the existing provision preview for newly authorized resources instead. This
command does not create, delete or modify a service-side resource.
