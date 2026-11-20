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

### Guided setup

Run the wizard in **your own terminal**, not the agent's. The agent can prepare the non-secret IDs as flags. Missing values are asked for, and the state is initialized if it is new.

~~~sh
python3 -B "$ANDA_PACKAGE/scripts/anda.py" --home "$ANDA_STATE" setup --project demo \
  --discord-app APP_ID --discord-guild SERVER_ID --owner-id YOUR_DISCORD_USER_ID --notion-parent PAGE_LINK
~~~

Three steps stay with you, because no public API covers them and the services require your own sign-in: create the Discord app and bot (turn on Message Content Intent), create the Notion internal integration (read and insert content) and connect it to the parent page, and click **Authorize** on the printed bot invite. The invite asks for View Channel, Send Messages and Read Message History. It also asks for Manage Channels, which is needed only to create the private channel. You can remove it from the bot's role afterwards.

The wizard then:

1. Takes each token only through hidden input. Tokens never go in arguments, files or output.
2. Runs read-only GET checks on the bot, server membership, owner and parent page, plus `doctor --live` for destinations already configured.
3. Saves the tokens in Windows Credential Manager or the macOS Keychain. On other systems, keep `ANDA_*_TOKEN` in the protected environment. The config stores only those names, and `doctor`, `worker` and `reconcile` read the stored value when the variable is unset.
4. Shows the `provision` plan and runs `--apply` only after you answer yes.
5. Prints the exec-form Claude Code hooks and MCP entry. After a second yes, it merges them into `<project>/.claude/settings.local.json` and `<project>/.mcp.json`. Global client settings are never edited, and Claude Code still asks you to approve them.

Then verify separately with `doctor --live` and `scripts/verify_claude_hooks.py --home NEW_EMPTY_DIR`.

Configure the routes you approve. With approved credentials, use `doctor --live` to check reads. Authorize delivery and any model execution separately. The first `worker --live` pass sets the starting point. Use one new synthetic input, run a bounded pass, and read back the expected receipts. [First-run guide](references/relay-first-run.md) · [Live verification steps](references/live-acceptance.ko.md)

## What each route does

- **Agent** — Project-scoped, read-only relay MCP exposes context and a change cursor. Approved native hooks can forward the visible prompt and reply of a selected turn. Native capture requires client-specific setup and trust.
- **Notion** — The approved page can archive relay records. Notion intake mirrors eligible new or changed top-level text paragraphs from the enrolled author to Discord without a model job. Each poll sees current text. Intermediate edits, deletions, and nested blocks are outside that route.
- **Discord** — The default request starts with `!anda `. The explicitly opted-in model can answer and deliver the response to both destinations. Plain-text intake requires separate consent and the scoped `all_text` setting.

Stable event IDs and the delivery queue prevent duplicate local records. For an uncertain send, check the original destination before retrying. [Operations and recovery](references/operations.md)

## Verification scope

- **Windows** — One private combined run verified a plain, allowlisted Discord request archived to Notion and a signed-in Codex response read back in Notion and Discord. It also verified one direct Notion paragraph mirrored to Discord and one approved Codex hook turn, with the prompt and reply each read back from both services. Eight distinct receipts were confirmed, with zero duplicate deliveries after reopening the ledger. A separate run verified the read-only MCP change cursor. [Windows verification](VERIFICATION-RELAY.md)
- **Claude and Gemini** — Evidence covers scoped MCP readback of one event. On Windows, one real Claude Code 2.1.322 turn confirmed native hooks (SessionStart, UserPromptSubmit, Stop): context injection from Korean/emoji memory, visible prompt and answer capture, and a shared `prompt_id` identity. Reproduce with `scripts/verify_claude_hooks.py`. The Claude execution adapter and Gemini hooks remain unverified.
- **macOS** — Isolated copy installation and MCP stdio passed. Manual restore passed with synthetic data on a separate Mac test candidate. The macOS temporary-path patch is integrated, and the full unit suite and smoke pass in CI (macos-latest). Live Notion/Discord delivery and an approved native Codex hook turn on macOS remain unverified.

Linux, unattended operation, and broader client coverage remain unverified. Keep the repository private while reviewing the [publication requirements](PUBLICATION.md).
