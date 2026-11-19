# ANDA

[한국어](README.ko.md)

![ANDA icon](assets/anda-icon-v01.png)

ANDA is a preview skill that connects agents, Notion, and Discord through a project-scoped SQLite ledger.

## Start here

1. Give your agent the **private Git repository link**. Ask it to read [SKILL.md](SKILL.md) and verify the revision before copying the package.
2. Select an approved **private Notion page** and **private Discord text channel**. Confirm the Notion integration, Discord bot, and allowed users. Review the requested permissions and keep credentials in a protected local environment. [Onboarding](references/onboarding.md)
3. Use Python 3.11 or later. From the repository root, use new, separate absolute directories for the package and state. Keep state outside the package and source control.

~~~sh
python3 -B scripts/check_release.py
python3 -B scripts/install.py --target "$ANDA_PACKAGE" --apply
python3 -B "$ANDA_PACKAGE/scripts/anda.py" --home "$ANDA_STATE" init --project demo
python3 -B "$ANDA_PACKAGE/scripts/anda.py" --home "$ANDA_STATE" doctor
~~~

Configure the routes you approve. With approved credentials, use `doctor --live` to check reads. Authorize delivery and any model execution separately. The first `worker --live` pass sets the starting point. Use one new synthetic input, run a bounded pass, and read back the expected receipts. [First-run guide](references/relay-first-run.md) · [Live verification steps](references/live-acceptance.ko.md)

## What each route does

- **Agent** — Project-scoped, read-only relay MCP exposes context and a change cursor. Approved native hooks can forward the visible prompt and reply of a selected turn. Native capture requires client-specific setup and trust.
- **Notion** — The approved page can archive relay records. Notion intake mirrors eligible new or changed top-level text paragraphs from the enrolled author to Discord without a model job. Each poll sees current text. Intermediate edits, deletions, and nested blocks are outside that route.
- **Discord** — The default request starts with `!anda `. The explicitly opted-in model can answer and deliver the response to both destinations. Plain-text intake requires separate consent and the scoped `all_text` setting.

Stable event IDs and the delivery queue prevent duplicate local records. For an uncertain send, check the original destination before retrying. [Operations and recovery](references/operations.md)

## Verification scope

- **Windows** — One private combined run verified a plain, allowlisted Discord request archived to Notion and a signed-in Codex response read back in Notion and Discord. It also verified one direct Notion paragraph mirrored to Discord and one approved Codex hook turn, with the prompt and reply each read back from both services. Eight distinct receipts were confirmed, with zero duplicate deliveries after reopening the ledger. A separate run verified the read-only MCP change cursor. [Windows verification](VERIFICATION-RELAY.md)
- **Claude and Gemini** — Evidence covers scoped MCP readback of one event. Model execution and native hook capture remain unverified for those clients.
- **macOS** — Isolated copy installation and MCP stdio passed. Manual restore passed with synthetic data on a separate Mac test candidate. The tested macOS temporary-path patch is pending integration. Live Notion/Discord delivery and an approved native Codex hook turn on macOS remain unverified.

Linux, unattended operation, and broader client coverage remain unverified. Keep the repository private while reviewing the [publication requirements](PUBLICATION.md).
