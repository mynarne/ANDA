"""Explicit consent and bounded source scopes for the relay draft."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re

from .core import AndaError, dumps, redact
from .notion_author import eligible, native_id, paragraph_text, same_page, validate_proof

ROUTES = {
    "discord.input": {"notion"},
    "discord.response": {"discord", "notion"},
    "notion.note": {"discord"},
    "hook.user": {"discord", "notion"},
    "hook.assistant": {"discord", "notion"},
}
MIRROR = re.compile(r"(?i)\bANDA:[0-9a-f-]{36}\b")
SNOW = re.compile(r"[1-9][0-9]{4,24}")


def validate(config):
    relay = config["relay"]
    if not isinstance(relay, dict) or set(relay) != {"paused", "projects"} or type(relay["paused"]) is not bool:
        raise AndaError("Relay needs an explicit pause flag and project map")
    projects = relay["projects"]
    if not isinstance(projects, dict) or set(projects) - set(config["projects"]):
        raise AndaError("Relay projects must exist in the ledger")
    for spec in projects.values():
        if not isinstance(spec, dict) or set(spec) - {"routes", "hooks"}:
            raise AndaError("Unknown relay project field")
        for name, route in spec.get("routes", {}).items():
            if (name not in ROUTES or set(route) != {"consent", "visibility", "destinations"}
                    or route["consent"] is not True or route["visibility"] != "private"
                    or not isinstance(route["destinations"], list)
                    or len(set(route["destinations"])) != len(route["destinations"])
                    or set(route["destinations"]) - ROUTES[name]):
                raise AndaError("Relay route needs private visibility, explicit consent and scoped destinations")
        for provider, hook in spec.get("hooks", {}).items():
            if (provider not in {"codex", "claude", "controlled"}
                    or set(hook) != {"cwd", "sessions"}
                    or not isinstance(hook["cwd"], str) or not Path(hook["cwd"]).is_absolute()
                    or redact(hook["cwd"]) != hook["cwd"]
                    or not isinstance(hook["sessions"], list) or not hook["sessions"]
                    or any(not stable_id(session) for session in hook["sessions"])):
                raise AndaError("Hook consent requires a supported client, exact directory and allowed session IDs")
    if set(config["destinations"]) != {"discord", "notion"} or set(config["intake"]) != {"discord", "notion"}:
        raise AndaError("Explicit connector settings are required")
    for name, spec in config["intake"].items():
        actors = spec["owner_ids" if name == "discord" else "user_ids"]
        if (not isinstance(actors, list) or len(actors) > 20
                or any(not stable_id(actor) for actor in actors)
                or (name == "discord" and any(not SNOW.fullmatch(actor) for actor in actors))):
            raise AndaError("Relay source allowlist is malformed or exceeds 20 actors")
        modes = {"discord": {"prefix", "all_text"}, "notion": {"notes"}}[name]
        if spec.get("enabled") and spec.get("mode", "prefix" if name == "discord" else "notes") not in modes:
            raise AndaError("Unsupported relay intake mode")
    notion = config["intake"]["notion"]
    if notion.get("enabled"):
        validate_proof(notion)
    discord = config["intake"]["discord"]
    if config["destinations"]["discord"]["enabled"] and not SNOW.fullmatch(str(discord.get("bot_id", ""))):
        raise AndaError("Relay Discord needs the explicit approved bot ID")
    if config["provider"]["name"] == "external" and config["provider"].get("enabled"):
        budget = config["provider"].get("approved_budget")
        if not isinstance(budget, str) or not re.fullmatch(r"[A-Z]{3} [1-9][0-9]{0,7}", budget):
            raise AndaError("An explicit approved budget is required for external execution")


def stable_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value)) and redact(value) == value


def route(config, project, name):
    return config["relay"]["projects"].get(project, {}).get("routes", {}).get(name)


def targets(config, project, name):
    policy = route(config, project, name)
    if not policy:
        return {}
    return {
        dest: spec["parent_page_id" if dest == "notion" else "channel_id"]
        for dest in policy["destinations"]
        if (spec := config["destinations"].get(dest, {})).get("enabled")
    }


def policy(config, project, name):
    spec = config["relay"]["projects"].get(project, {})
    source = name.split(".", 1)[0]
    if source == "hook":
        scope = spec.get("hooks", {})
    elif source == "discord":
        scope = [config["intake"]["discord"], config["destinations"]["discord"]["channel_id"],
                 route(config, project, "discord.response"), targets(config, project, "discord.response")]
    else:
        scope = config["intake"]["notion"]
    active_targets = targets(config, project, name)
    audience = None
    if "discord" in active_targets:
        discord = config["intake"]["discord"]
        audience = {"bot_id": discord["bot_id"], "owners": sorted(discord["owner_ids"])}
    return hashlib.sha256(dumps([route(config, project, name), scope, active_targets, audience]).encode()).hexdigest()


def job_current(ledger, ident):
    row = ledger.db.execute(
        "SELECT e.project,r.route,r.policy FROM relay_events r JOIN events e ON e.id=r.event_id WHERE e.id=?",
        (ident,)).fetchone()
    return bool(row and row["route"] == "discord.input"
                and row["policy"] == policy(ledger.config, row["project"], row["route"])
                and targets(ledger.config, row["project"], "discord.input") == {
                    "notion": ledger.config["destinations"]["notion"]["parent_page_id"]}
                and set(targets(ledger.config, row["project"], "discord.response")) == {"discord", "notion"})


def delivery_current(ledger, event, delivery):
    row = ledger.db.execute("SELECT route,policy FROM relay_events WHERE event_id=?", (event["id"],)).fetchone()
    if "relay" not in ledger.config:
        return row is None
    return bool(row and not ledger.config["relay"]["paused"]
                and row["policy"] == policy(ledger.config, event["project"], row["route"])
                and targets(ledger.config, event["project"], row["route"]).get(delivery["destination"]) == delivery["target_id"])


def enqueue_response(ledger, job, event, ident):
    row = ledger.db.execute("SELECT response_targets FROM relay_events WHERE event_id=?", (job["id"],)).fetchone()
    if row:
        name = "discord.response"
        ledger.db.execute("INSERT INTO relay_events(event_id,route,policy) VALUES(?,?,?)",
                          (ident, name, policy(ledger.config, event["project"], name)))
        ledger.enqueue(ident, json.loads(row["response_targets"]), name)


def allowed_text(text, config=None):
    if not isinstance(text, str) or not text.strip() or MIRROR.search(text):
        return False
    # Reject recognized credential-bearing turns in full rather than sending partial context.
    if config:
        names = config["provider"].get("pass_env", [])
        if any((value := os.environ.get(name)) and value in text for name in names):
            return False
    if redact(text) != text:
        return False
    return len(text.encode("utf-8")) <= 32000


def record(ledger, project, source, container, ident, text, name, *, role="user", job=False, in_transaction=False):
    if not route(ledger.config, project, name) or not allowed_text(text, ledger.config):
        return None
    return ledger.record(project, source, container, ident, text, role=role,
                         kind="request" if job else "note", job=job, relay_route=name, in_transaction=in_transaction)


def capture(ledger, project, provider, payload, event_id=None):
    spec = ledger.config["relay"]["projects"].get(project, {}).get("hooks", {}).get(provider)
    if ledger.config["relay"]["paused"] or not spec or payload.get("project", project) != project:
        return {"state": "out_of_scope", "persisted": False}
    if provider == "controlled" and payload.get("schema") == "anda.event.v1":
        phases = {"user_prompt": ("user", "text"), "assistant_message": ("assistant", "text")}
        phase = payload.get("event_type")
        session = payload.get("conversation_id")
        ident = payload.get("event_id")
    elif provider in {"codex", "claude"} and "schema" not in payload:
        phases = {"UserPromptSubmit": ("user", "prompt"), "Stop": ("assistant", "last_assistant_message")}
        phase = payload.get("hook_event_name")
        session = payload.get("session_id")
        # Claude Code identifies each user prompt with prompt_id (v2.1.196+); it has no turn_id.
        ident = payload.get("turn_id") if provider == "codex" else event_id or payload.get("prompt_id")
        if payload.get("stop_hook_active") is True:
            return {"state": "recursive_stop", "persisted": False}
    else:
        return {"state": "unsupported", "persisted": False}
    cwd = payload.get("cwd")
    if (not isinstance(cwd, str) or not Path(cwd).is_absolute()
            or os.path.normcase(str(Path(cwd).resolve())) != os.path.normcase(str(Path(spec["cwd"]).resolve()))
            or not stable_id(session) or session not in spec["sessions"]):
        return {"state": "out_of_scope", "persisted": False}
    if phase not in phases or not stable_id(ident):
        return {"state": "unsupported_or_missing_stable_id", "persisted": False}
    role, field = phases[phase]
    text = payload.get(field)
    source_id = ident + ":" + phase
    if phase == "Stop" and isinstance(text, str):
        # One prompt can end more than once (e.g. after background work); same text still deduplicates.
        source_id += ":" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    result = record(ledger, project, "hook:" + provider, session, source_id, text, "hook." + role, role=role)
    return {**result, "persisted": True} if result else {"state": "ignored", "persisted": False}


def discord_scope(connectors, channel, owners, bot_id):
    bot = connectors.call("discord", "GET", "/users/@me")
    data = connectors.call("discord", "GET", "/channels/" + channel)
    guild = data.get("guild_id")
    if (bot.get("bot") is not True or bot.get("id") != bot_id
            or data.get("id") != channel or data.get("type") != 0 or not guild):
        raise AndaError("Discord relay channel or bot identity not verified")
    rows = data.get("permission_overwrites")
    if not isinstance(rows, list):
        raise AndaError("Discord channel permissions unavailable")
    everyone_denied = False
    for row in rows:
        allow, deny = int(row["allow"]), int(row["deny"])
        if row["type"] == 0 and row["id"] == guild:
            everyone_denied = bool(deny & 1024)
        if allow & 1024 and (row["type"] != 1 or row["id"] not in {*owners, bot["id"]}):
            raise AndaError("Discord channel view scope exceeds the allowlist")
    if not everyone_denied:
        raise AndaError("Discord requires explicit everyone view denial")
    return bot["id"]


def discord_message(message, channel, owners, bot_id):
    actor = message.get("author", {})
    return (message.get("channel_id") == channel and message.get("type") in {0, 19}
            and not message.get("webhook_id") and not message.get("application_id")
            and not actor.get("bot") and not actor.get("system") and actor.get("id") != bot_id
            and actor.get("id") in owners)


def notion_notes(ledger, connectors, spec):
    project, page = spec["project"], spec["page_id"]
    if not route(ledger.config, project, "notion.note"):
        return {"state": "disabled"}
    proof = validate_proof(spec)
    data = connectors.call("notion", "GET", "/pages/" + page)
    if (str(data.get("id", "")).replace("-", "") != page.replace("-", "")
            or "public_url" not in data or data["public_url"] is not None
            or data.get("archived") or data.get("in_trash")):
        raise AndaError("Notion source is not the exact active private page")
    bot = connectors.call("notion", "GET", "/users/me")
    if bot.get("type") != "bot" or native_id(bot.get("id")) != proof["bot"]:
        raise AndaError("Notion integration identity not verified")
    blocks = connectors.pages("notion", "/blocks/" + page + "/children?page_size=100")
    scope = hashlib.sha256(dumps(spec).encode()).hexdigest()[:24]
    key = "relay:notion:" + project + ":" + page + ":" + scope
    count = 0
    with ledger.transaction():
        baseline = ledger.cursor(key) is None
        for block in sorted(blocks, key=lambda item: (str(item.get("last_edited_time", "")), str(item.get("id", "")))):
            if block.get("type") != "paragraph" or block.get("archived") or block.get("in_trash"):
                continue
            block_id = block.get("id")
            revision = block.get("last_edited_time")
            if not native_id(block_id) or not isinstance(revision, str) or not same_page(block, page):
                raise AndaError("Notion block scope or revision not verified")
            edited = datetime.fromisoformat(revision.replace("Z", "+00:00"))
            if edited.tzinfo is None:
                raise AndaError("Notion revision has no timezone")
            revision = edited.isoformat(timespec="microseconds")
            text = paragraph_text(block)
            cursor_key = key + ":" + block_id
            previous = ledger.cursor(cursor_key)
            fingerprint = hashlib.sha256(text.encode()).hexdigest() if allowed_text(text, ledger.config) else "-"
            if previous:
                from json import loads
                last_time, last_text = loads(previous)
                if datetime.fromisoformat(last_time) > edited or (last_time, last_text) == (revision, fingerprint):
                    continue
            if (not baseline and fingerprint != "-" and (not previous or fingerprint != last_text)
                    and eligible(block, text, proof)):
                accepted = record(ledger, project, "notion", page,
                                  block_id + ":" + revision + ":" + fingerprint, text, "notion.note", in_transaction=True)
                count += bool(accepted and not accepted["duplicate"])
            ledger.set_cursor(cursor_key, dumps([revision, fingerprint]))
        ledger.set_cursor(key, "ready")
    return {"state": "baseline" if baseline else "pulled", "count": count}


def setup_status(ledger):
    """Report progress without exposing configured addresses or credentials."""
    config = ledger.config
    destinations = {
        name: {"configured": spec["enabled"], "credential_available": bool(os.environ.get(spec["token_env"]))}
        for name, spec in config["destinations"].items()
    }
    relay = config.get("relay")
    return {
        "state": "configured" if relay else "relay_configuration_missing",
        "paused": relay["paused"] if relay else None,
        "projects_with_consent": len(relay["projects"]) if relay else 0,
        "destinations": destinations,
        "source_baselines": {
            "discord": ledger.db.execute("SELECT count(*) FROM cursors WHERE key LIKE 'relay:discord:%'").fetchone()[0],
            "notion": ledger.db.execute("SELECT count(*) FROM cursors WHERE key LIKE 'relay:notion:%' AND value='ready'").fetchone()[0],
        },
        "provider_enabled": config["provider"]["enabled"],
        "network_calls": False,
        "live_access": "not_checked",
    }
