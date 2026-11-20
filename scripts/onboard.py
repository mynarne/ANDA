#!/usr/bin/env python3
"""Adopt already approved private endpoints without external writes or global changes."""
import argparse
from contextlib import closing
import getpass
import os
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit
from uuid import UUID
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from anda.connectors import DeliveryError, Transport
from anda.core import AndaError, Ledger, dumps, example_config, unsafe_link, validate_config

SECRETS = {"notion": "ANDA_NOTION_TOKEN", "discord": "ANDA_DISCORD_TOKEN"}
SNOW = r"[1-9][0-9]{16,21}"
OWNER = r"[a-z0-9_.]{2,32}"
VIEW, SEND, HISTORY = 1 << 10, 1 << 11, 1 << 16
BOT_REQUIRED = VIEW | SEND | HISTORY
PAGE = r"[0-9A-Fa-f]{32}|[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"


class Blocked(AndaError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def need(ok, code, message):
    if not ok:
        raise Blocked(code, message)


def snow(value):
    return isinstance(value, str) and re.fullmatch(SNOW, value) is not None


def notion_id(value):
    need(isinstance(value, str), "notion_page", "An exact Notion page ID or official link is required.")
    if re.fullmatch(PAGE, value):
        return str(UUID(value))
    u = urlsplit(value)
    need(u.scheme == "https" and u.netloc in {"app.notion.com", "www.notion.so", "notion.so"},
         "notion_page", "Use an exact Notion page ID or official Notion link.")
    match = re.search(r"(?:^|/|-)([0-9a-fA-F]{32})(?:/)?$", u.path)
    need(match, "notion_page", "No exact page ID in the chosen Notion link.")
    return str(UUID(match.group(1)))


def discord_ids(value):
    if snow(value):
        return None, value
    u = urlsplit(value)
    pattern = rf"/channels/({SNOW})/({SNOW})/?"
    m = re.fullmatch(pattern, u.path)
    need(u.scheme == "https" and u.netloc in {"discord.com", "discordapp.com"} and m,
         "discord_channel", "Use an exact Discord text-channel ID or official server channel link.")
    return m.group(1), m.group(2)


def prepare_scope(spec, browser_owner, run_home, enable_codex=False):
    project, page, chan, bot = spec
    cfg = example_config(project)
    validate_config(cfg)
    page = notion_id(page)
    guild, chan = discord_ids(chan)
    need(snow(bot), "bot_id", "Expected Discord test bot ID is required.")
    need(snow(browser_owner) or (isinstance(browser_owner, str) and re.fullmatch(OWNER, browser_owner)),
         "owner", "Supply the intended human Discord username (not a display name), or user ID.")
    home = Path(run_home).expanduser()
    need(home.is_absolute() and not home.is_symlink() and len(home.parts) >= 3
         and '..' not in home.parts and not home.exists() and not any(unsafe_link(p) for p in home.parents) and safe_destination(home),
         "state_path", "Choose a new absolute dedicated ANDA state directory.")
    return {"project": project, "notion_page": page, "discord_channel": chan, "discord_guild": guild,
            "discord_bot": bot, "owner_selector": browser_owner, "enable_codex": enable_codex, "home": home}


def flags(value):
    need(isinstance(value, str) and re.fullmatch(r"[0-9]{1,38}", value) is not None,
         "discord_privacy", "Discord returned malformed permission bits.")
    return int(value)


def check(scope, secrets, transport=None):
    api = Transport() if transport is None else transport

    def get(service, path, label):
        try:
            result = api.request(service, "GET", path, secrets[service])
        except DeliveryError as exc:
            if exc.state == "blocked" or str(exc).endswith("HTTP 404"):
                raise Blocked(label, "The expected identity or selected resource is not accessible.") from None
            raise Blocked(label, "Read-only API preflight did not complete; no write was attempted.") from None
        need(isinstance(result, dict), label, "A scoped read returned an unexpected response.")
        return result

    notion_bot = get("notion", "/users/me", "notion_identity")
    need(notion_bot.get("type") == "bot", "notion_identity",
         "A connection bot is required; do not use a personal access token.")
    page = get("notion", "/pages/" + scope["notion_page"], "notion_page_access")
    need(page.get("object") == "page" and isinstance(page.get("id"), str)
         and page["id"].replace("-", "").lower() == scope["notion_page"].replace("-", "")
         and "public_url" in page and page["public_url"] is None
         and not page.get("archived") and not page.get("in_trash"),
         "notion_privacy", "Notion did not confirm the exact active, non-public test page.")

    bot = scope["discord_bot"]
    me = get("discord", "/users/@me", "discord_identity")
    need(me.get("bot") is True and me.get("id") == bot, "discord_identity",
         "Credential did not match the designated Discord test bot.")
    chan = scope["discord_channel"]
    channel = get("discord", "/channels/" + chan, "discord_channel_access")
    guild = channel.get("guild_id")
    need(channel.get("id") == chan and channel.get("type") == 0 and snow(guild)
         and scope["discord_guild"] in (None, guild), "discord_scope",
         "Discord did not confirm the exact selected server text channel.")

    raw = channel.get("permission_overwrites")
    need(isinstance(raw, list) and 1 <= len(raw) <= 100, "discord_privacy",
         "Explicit private channel permission overwrites are required.")
    rules = {}
    for r in raw:
        need(isinstance(r, dict) and type(r.get("type")) is int and r["type"] in (0, 1)
             and snow(r.get("id")) and (r["type"], r["id"]) not in rules,
             "discord_privacy", "Discord returned ambiguous permission overwrites.")
        allow, deny = flags(r.get("allow")), flags(r.get("deny"))
        need(not (allow & deny), "discord_privacy", "A permission is allowed and denied simultaneously.")
        rules[(r["type"], r["id"])] = (allow, deny)

    require_everyone = rules.get((0, guild), (0, 0))[1] & VIEW
    need(require_everyone and all(t == 1 or not (a & VIEW) for (t, _), (a, _) in rules.items()),
         "discord_privacy", "The channel must deny @everyone and every role must lack an explicit view grant.")
    a, d = rules.get((1, bot), (0, 0))
    need(a & BOT_REQUIRED == BOT_REQUIRED and not (d & BOT_REQUIRED), "discord_bot_channel_permissions",
         "The test bot needs explicit view, send, and history permission here.")
    candidates = [i for (t, i), (a, _) in rules.items() if t == 1 and i != bot and a & VIEW]
    need(len(candidates) <= 1, "discord_privacy", "Another member has channel access; select a narrower channel.")
    if candidates:
        owner_id = candidates[0]
    else:
        # Server owners can view a private channel even without an individual override.
        g = get("discord", "/guilds/" + guild, "discord_owner")
        need(g.get("id") == guild and snow(g.get("owner_id")), "discord_owner",
             "Discord did not confirm the server owner. No owner was inferred.")
        owner_id = g["owner_id"]
    owner = get("discord", "/guilds/" + guild + "/members/" + owner_id, "discord_owner")
    user = owner.get("user")
    need(isinstance(user, dict) and user.get("id") == owner_id and user.get("bot", False) is False,
         "discord_owner", "The allowed account is not a verified human server member.")
    selector = scope["owner_selector"]
    need(selector == (owner_id if snow(selector) else user.get("username")),
         "discord_owner", "The verified human did not match the intended Discord account.")

    return {"discord_user": owner_id, "notion_page": scope["notion_page"], "discord_channel": chan,
            "discord_bot": bot, "discord_guild": guild}


def safe_destination(home):
    # Callers reject links with unsafe_link first, so resolve() only canonicalizes OS aliases
    # (macOS /var -> /private/var, Windows 8.3 short names) before the package check.
    return not home.resolve(strict=False).is_relative_to(Path(__file__).resolve().parents[1])


def local_config(scope, proof):
    home = scope["home"]
    # Revalidate after network preflight. Never open or reuse an existing state.
    need(not home.exists() and not home.is_symlink() and not any(unsafe_link(p) for p in home.parents) and safe_destination(home),
         "state_path", "The state path changed or already exists. Nothing was overwritten.")
    cfg = example_config(scope["project"])
    cfg["destinations"]["notion"].update(enabled=True, parent_page_id=proof["notion_page"])
    cfg["destinations"]["discord"].update(enabled=True, channel_id=proof["discord_channel"])
    cfg["intake"]["discord"].update(enabled=True, owner_ids=[proof["discord_user"]])
    # Fresh setups default to no model use; a live worker always needs a separate explicit opt-in.
    cfg["provider"].update(enabled=scope["enable_codex"], max_calls_per_day=1)
    validate_config(cfg)
    home.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = home / "config.json"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT, 0o600), "w", encoding="utf-8") as f:
        f.write(dumps(cfg) + "\n")
    with closing(Ledger(home, cfg)) as ledger:
        ledger.audit("adopt_existing_private_endpoints", scope["project"], "Only read-only endpoint checks were performed")


def cli(argv=None, *, env=None, transport=None):
    env = os.environ if env is None else env
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", required=True, help="New absolute local state path")
    p.add_argument("--project", required=True)
    p.add_argument("--notion-page", required=True, help="Exact private page ID or Notion page link")
    p.add_argument("--discord-channel", required=True, help="Exact private channel ID or Discord channel link")
    p.add_argument("--discord-bot", required=True, help="Designated test bot user ID")
    p.add_argument("--discord-owner", required=True, help="Human Discord username or user ID")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only API preflight; no files or external writes")
    mode.add_argument("--apply", action="store_true", help="Preflight then write a new local config; no external writes")
    p.add_argument("--prompt-secrets", action="store_true", help="Owner's private local TTY, never the agent's terminal")
    p.add_argument("--enable-codex", action="store_true", help="Enable Codex in new config; never invoke the model")
    a = p.parse_args(argv)
    status = {"external_writes": 0, "model_calls": 0, "network_reads": False}
    try:
        scope = prepare_scope((a.project, a.notion_page, a.discord_channel, a.discord_bot),
                    a.discord_owner, a.home, a.enable_codex)
        el = {service: env.get(key) for service, key in SECRETS.items()}
        missing = [SECRETS[key] for key, value in el.items() if not value]
        if not (a.check or a.apply):
            print(dumps(dict(status, state="plan", missing_secret_names=missing,
                             writes_local_config=False, provider_enabled=False,
                             codex_available=bool(shutil.which("codex")))))
            return 0
        if missing and a.prompt_secrets:
            need(sys.stdin.isatty(), "private_terminal", "Enter secrets only in a private local terminal.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                for service, key in SECRETS.items():
                    if not el[service]:
                        try:
                            el[service] = getpass.getpass(key + " (hidden): ").strip()
                        except getpass.GetPassWarning:
                            raise Blocked("private_terminal", "Terminal cannot guarantee hidden input.") from None
        missing = [SECRETS[key] for key, value in el.items() if not value]
        if missing:
            print(dumps(dict(status, state="blocked_missing_credentials", missing_secret_names=missing)))
            return 2
        status["network_reads"] = True
        proof = check(scope, el, transport)
        if a.apply:
            local_config(scope, proof)
        print(dumps(dict(status, state="configured" if a.apply else "read_preflight_verified",
                         ids_verified=proof, write_permissions_verified=False,
                         writes_local_config=a.apply, provider_enabled=bool(a.apply and a.enable_codex),
                         privacy_limit="Discord server owners and administrators can bypass channel restrictions.")))
        return 0
    except Blocked as exc:
        print(dumps(dict(status, state="blocked", code=exc.code, detail=str(exc))))
    except (AndaError, ValueError, OSError, EOFError, KeyboardInterrupt):
        print(dumps(dict(status, state="blocked", code="setup_error",
                         detail="Setup stopped; raw diagnostics were not displayed.")))
    return 2


if __name__ == "__main__":
    raise SystemExit(cli())
