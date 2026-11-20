"""Opt-in provisioning of NEW resources only; durable intent before each POST."""
import copy
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import shlex
import tempfile
import urllib.parse
import warnings
from . import credentials
from .core import AndaError, dumps, unsafe_link, validate_config
from .connectors import Connectors, DeliveryError

def atomic_write(path, text):
    path = Path(path)
    if path.is_symlink() or unsafe_link(path):
        raise AndaError("Symlink target rejected")
    fd, temp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def atomic_config(home, config):
    validate_config(config)
    atomic_write(Path(home) / "config.json", dumps(config) + "\n")

def client_config(ledger, project, client, ssh_host="", remote_script="", remote_home="", agent_label=""):
    """Native local stdio or SSH stdio to ONE trusted host. No network MCP listener."""
    ledger.project(project)
    label = agent_label or client
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",label):raise AndaError("Bounded configured agent label required")
    script = str(Path(__file__).resolve().parents[2] / "scripts" / "anda.py")
    args = [script, "--home", str(ledger.home), "mcp", "--agent", label, "--project", project]
    entry = {"command": sys.executable, "args": args}
    if ssh_host:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", ssh_host):
            raise AndaError("Use an existing SSH host alias, not an arbitrary shell expression")
        if not remote_script.startswith("/") or not remote_home.startswith("/") or any("\n" in x or "\x00" in x for x in [remote_script, remote_home]):
            raise AndaError("Explicit absolute POSIX remote script and state paths required")
        remote = shlex.join(["python3", remote_script, "--home", remote_home, "mcp", "--agent", label, "--project", project])
        entry = {"command": "ssh", "args": ["-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", ssh_host, remote]}
    elif remote_script or remote_home:
        raise AndaError("Remote paths require an explicit SSH host alias")
    # Emit a reviewable snippet. Never merge global settings, install hooks or start a daemon.
    if client in {"codex", "grok"}:
        return "[mcp_servers.anda]\ncommand = " + json.dumps(entry["command"], ensure_ascii=False) + "\nargs = " + json.dumps(entry["args"], ensure_ascii=False) + "\n"
    if client in {"claude", "gemini", "generic"}:
        return dumps({"mcpServers": {"anda": entry}})
    raise AndaError("Unsupported MCP client")

def provision(ledger, project, notion_parent="", guild="", owner="", notion_user="", apply=False, connectors=None):
    ledger.project(project)
    if not notion_parent and not guild:
        raise AndaError("Select at least one explicit parent or guild")
    if notion_parent and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", notion_parent):
        raise AndaError("Invalid Notion parent")
    if guild and (not re.fullmatch(r"[0-9]{5,25}", guild) or not re.fullmatch(r"[0-9]{5,25}", owner)):
        raise AndaError("Discord guild and human owner IDs required")
    if notion_user and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", notion_user):
        raise AndaError("Invalid Notion user ID")
    params = {"project": project, "notion_parent": notion_parent, "guild": guild, "owner": owner, "notion_user": notion_user}
    scope = hashlib.sha256(dumps(params).encode()).hexdigest()[:24]
    plan = {"state": "plan", "scope": scope, "parameters": params, "network_calls": False,
            "new_resources": (["Notion root, requests, releases, decisions, memory pages"] if notion_parent else []) +
                             (["Discord private category and workroom text channel"] if guild else []),
            "existing_resources_edited": False,
            "manual_steps": ["Create/sign in to official AI clients", "Create and install your own Discord bot with scoped permissions",
                             "Share the selected Notion parent with your integration", "Supply secrets through protected environment variables",
                             "Approve the printed client MCP configuration in each client"]}
    if not apply:
        return plan
    setup_scope = ledger.config.get("onboarding", {}).get("scope")
    if setup_scope and setup_scope != scope:
        raise AndaError("Existing onboarding scope differs; use a fresh dedicated state")
    if any(x.get("enabled") for x in ledger.config["destinations"].values()) and setup_scope != scope:
        raise AndaError("Refusing to replace an existing live setup; use a fresh dedicated state")
    cfg = copy.deepcopy(ledger.config)
    for name, enabled in [("notion", bool(notion_parent)), ("discord", bool(guild))]:
        if enabled:
            cfg["destinations"][name]["enabled"] = True
    api = connectors or Connectors(cfg)
    ledger.db.execute("CREATE TABLE IF NOT EXISTS provisioning(scope TEXT,step TEXT,state TEXT,receipt TEXT,body_hash TEXT,PRIMARY KEY(scope,step))")

    def create(step, service, path, body):
        digest = hashlib.sha256(dumps(body).encode()).hexdigest()
        with ledger.transaction():
            row = ledger.db.execute("SELECT * FROM provisioning WHERE scope=? AND step=?", (scope, step)).fetchone()
            if row:
                if row["body_hash"] != digest:
                    raise AndaError("Provisioning plan changed")
                if row["state"] == "confirmed":
                    return row["receipt"]
                if row["state"] != "retryable":
                    raise AndaError("Provisioning outcome unresolved; inspect the external resource before any new plan")
            ledger.db.execute("INSERT OR REPLACE INTO provisioning VALUES(?,?,?,?,?)", (scope, step, "unknown", None, digest))
            ledger.audit("provision_intent", scope + ":" + step, "New resource only; no blind replay")
        try:
            result = api.call(service, "POST", path, body)
            receipt = result.get("id")
            pattern = r"[0-9a-fA-F-]{32,36}" if service == "notion" else r"[0-9]{5,25}"
            if not isinstance(receipt, str) or not re.fullmatch(pattern, receipt):
                raise DeliveryError("unknown", "Provisioning receipt missing")
        except DeliveryError as exc:
            # Only an explicit rejection/rate limit permits a later manual retry.
            state = "retryable" if exc.state in {"pending", "blocked", "failed"} else "unknown"
            ledger.db.execute("UPDATE provisioning SET state=? WHERE scope=? AND step=?", (state, scope, step))
            raise AndaError("Provisioning " + state + "; credentials/permissions or external receipt need review") from None
        ledger.db.execute("UPDATE provisioning SET state='confirmed',receipt=? WHERE scope=? AND step=?", (receipt, scope, step))
        return receipt

    receipts = {}
    if notion_parent:
        me = api.call("notion", "GET", "/users/me")
        if me.get("type") != "bot":
            raise AndaError("Expected a Notion integration identity")
        # Verify access before any writes.
        parent_page = api.call("notion", "GET", "/pages/" + notion_parent)
        if parent_page.get("public_url"):
            raise AndaError("Choose a non-public Notion parent; child pages inherit parent access")

        def page(step, parent, title, paragraphs):
            children = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": text}}]}} for text in paragraphs]
            body = {"parent": {"page_id": parent},
                    "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
                    "children": children}
            return create(step, "notion", "/pages", body)

        root = page("notion-root", notion_parent, "ANDA · " + project + " · " + scope,
                    ["Agent + Notion + Discord Automation", "공유 기억 · 요청 · 릴리즈 · 결정 기록",
                     "에이전트 기록은 검증 완료를 의미하지 않습니다. 작업 결과와 검증 근거를 구분합니다."])
        requests = page("notion-requests", root, "요청함 · Requests",
                        ["!anda 접두어로 새 문단에 요청을 작성합니다. 첫 실행 시 기존 요청은 수행하지 않습니다.",
                         "직접 문단만 수집합니다. DB, 첨부, 댓글, 중첩 블록은 수집하지 않습니다."])
        releases = page("notion-releases", root, "릴리즈 노트 · Releases",
                        ["결과는 이 페이지 아래 새 하위 페이지로 누적됩니다. 기존 기록은 덮어쓰지 않습니다.",
                         "기록 저장 / 실제 연결 / 테스트 통과 / 배포 완료는 서로 다른 상태입니다."])
        decisions = page("notion-decisions", root, "결정 · Decisions",
                         ["확정된 사용자 결정은 로컬 operator 명령에서만 기록합니다.",
                          "일반 에이전트 메모는 사용자 승인으로 간주하지 않습니다."])
        memory_page = page("notion-memory", root, "메모리 · Memory",
                           ["대화 원문과 별도로 결정·선호·진행·맥락을 보존합니다. 각 정정은 새 기록으로 누적합니다.",
                            "프로젝트 범위 · 출처 이벤트 · 유효 시점 · 상태 · 이전 revision을 기록합니다.",
                            "에이전트가 추정한 결정·선호는 제안입니다. 사용자 확정과 실제 검증을 구분합니다.",
                            "이 페이지의 문서는 revision 아카이브이며, 현재값·만료·정정 판단은 공통 원장의 MCP 조회를 기준으로 합니다."])
        receipts.update(notion_root=root, notion_requests=requests, notion_releases=releases, notion_decisions=decisions, notion_memory=memory_page)
        cfg["destinations"]["notion"]["memory_page_id"] = memory_page
        cfg["destinations"]["notion"]["parent_page_id"] = releases
        cfg["intake"]["notion"].update(page_id=requests, user_ids=[notion_user] if notion_user else [], enabled=bool(notion_user), project=project)
    if guild:
        me = api.call("discord", "GET", "/users/@me")
        if not me.get("bot") or not re.fullmatch(r"[0-9]{5,25}", str(me.get("id", ""))):
            raise AndaError("Expected a Discord bot identity")
        member = api.call("discord", "GET", "/guilds/" + guild + "/members/" + owner)
        if member.get("user", {}).get("id") != owner or member.get("user", {}).get("bot"):
            raise AndaError("Owner must be a human member of the selected guild")
        # Explicitly deny role overwrites as well: @everyone deny alone can be overridden by another role.
        roles = api.call("discord", "GET", "/guilds/" + guild + "/roles")
        if not isinstance(roles, list) or len(roles) > 95:
            raise AndaError("Cannot safely fit the guild role privacy overwrites")
        overwrites = [{"id": role["id"], "type": 0, "allow": "0", "deny": "1024"} for role in roles]
        if guild not in [x["id"] for x in overwrites]:
            raise AndaError("Guild everyone role missing")
        allow = str(1024 + 2048 + 65536)  # VIEW_CHANNEL, SEND_MESSAGES, READ_MESSAGE_HISTORY
        overwrites.extend([{"id": ident, "type": 1, "allow": allow, "deny": "0"} for ident in sorted({owner, me["id"]})])
        path = "/guilds/" + guild + "/channels"
        category = create("discord-category", "discord", path, {"name": "ANDA-" + project, "type": 4, "permission_overwrites": overwrites})
        channel = create("discord-workroom", "discord", path,
                         {"name": "anda-workroom", "type": 0, "parent_id": category, "permission_overwrites": overwrites,
                          "topic": "ANDA " + scope + " · !anda 요청 · 결과와 검증 근거 구분"})
        receipts.update(discord_category=category, discord_channel=channel)
        cfg["destinations"]["discord"]["channel_id"] = channel
        cfg["intake"]["discord"].update(enabled=True, owner_ids=[owner], project=project)
    cfg["onboarding"] = {"scope": scope, "receipts": receipts}
    atomic_config(ledger.home, cfg)
    ledger.config = cfg
    ledger.audit("provision_configured", scope, "New resource IDs stored; provider execution remains opt-in")
    return {"state": "configured", "scope": scope, "receipts": receipts, "live": True,
            "provider_enabled": cfg["provider"]["enabled"],
            "warning": "Discord server owner/administrators can still access private channels. Ordinary non-admin roles are denied."}

# Bot invite: VIEW_CHANNEL, SEND_MESSAGES, READ_MESSAGE_HISTORY. Creating the private
# category/channel additionally needs MANAGE_CHANNELS (Discord's Create Guild Channel rule).
BOT_PERMISSIONS, MANAGE_CHANNELS = 1024 | 2048 | 65536, 16
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")

def invite_url(app_id, guild="", create_channel=False):
    if not re.fullmatch(r"[0-9]{5,25}", app_id) or (guild and not re.fullmatch(r"[0-9]{5,25}", guild)):
        raise AndaError("Discord application and server IDs are numeric")
    query = {"client_id": app_id, "scope": "bot",
             "permissions": str(BOT_PERMISSIONS | (MANAGE_CHANNELS if create_channel else 0))}
    if guild:
        query.update(guild_id=guild, disable_guild_select="true")
    return "https://discord.com/oauth2/authorize?" + urllib.parse.urlencode(query)

def notion_page_id(value):
    if not value or re.fullmatch(r"[0-9a-fA-F-]{32,36}", value):
        return value
    link = urllib.parse.urlsplit(value)
    match = re.search(r"([0-9a-fA-F]{32})/?$", link.path)
    if link.scheme != "https" or link.netloc not in {"www.notion.so", "notion.so", "app.notion.com"} or not match:
        raise AndaError("Use the Notion page ID or its official page link")
    return match.group(1)

def claude_settings(ledger, project):
    """Exec-form (command + args) hook and MCP entries: no shell, so no quoting differences per OS."""
    script = str(Path(__file__).resolve().parents[2] / "scripts" / "anda.py")
    hook = {"type": "command", "command": sys.executable, "timeout": 30,
            "args": [script, "--home", str(ledger.home), "hook", "--project", project, "--provider", "claude", "--native"]}
    return ({event: [{"hooks": [hook]}] for event in HOOK_EVENTS},
            json.loads(client_config(ledger, project, "claude"))["mcpServers"]["anda"])

def _json_object(path):
    if unsafe_link(path):
        raise AndaError("Symlink target rejected")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise AndaError(path.name + " is not a JSON object; nothing was written")
    return data

def write_claude_project(folder, hooks, server, state_home):
    """Merge into <project>/.claude/settings.local.json and <project>/.mcp.json only; never global settings."""
    root = Path(folder).expanduser()
    if not root.is_absolute() or not root.is_dir() or any(unsafe_link(p) for p in [root, *root.parents]):
        raise AndaError("Choose an existing absolute Claude Code project folder without user-made links")
    # Canonical paths: /var/x and /private/var/x (macOS) or 8.3 short names (Windows) are one folder.
    real = root.resolve()
    if any(real == base or base in real.parents for base in (Path(__file__).resolve().parents[2], Path(state_home).resolve())):
        raise AndaError("Keep the Claude Code project folder outside the ANDA package and state")
    settings, mcp = root / ".claude" / "settings.local.json", root / ".mcp.json"
    if unsafe_link(settings.parent):
        raise AndaError("Symlink target rejected")
    local, shared = _json_object(settings), _json_object(mcp)
    events, servers = local.setdefault("hooks", {}), shared.setdefault("mcpServers", {})
    if not isinstance(events, dict) or not isinstance(servers, dict) or not all(isinstance(events.get(e, []), list) for e in hooks):
        raise AndaError("Unexpected existing Claude settings shape; nothing was written")
    for event, groups in hooks.items():
        current = events.setdefault(event, [])
        current.extend([group for group in groups if group not in current])
    servers["anda"] = server
    settings.parent.mkdir(exist_ok=True)
    for path, data in ((settings, local), (mcp, shared)):
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return [str(settings), str(mcp)]

def _hidden(text):
    if not sys.stdin.isatty():
        raise AndaError("Enter tokens only in your own private terminal")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(text)
        except (getpass.GetPassWarning, EOFError):
            raise AndaError("This terminal cannot hide input; nothing was stored") from None

def _read_checks(api, notion_parent, guild, owner, bot):
    """doctor --live style GETs on the selected new targets, before anything is stored or created."""
    def get(service, path, what):
        try:
            result = api.call(service, "GET", path)
        except DeliveryError as exc:
            raise AndaError(what + " could not be read (" + exc.state + "); nothing was stored or created") from None
        if not isinstance(result, dict):
            raise AndaError(what + " returned an unexpected response; nothing was stored or created")
        return result
    if notion_parent:
        if get("notion", "/users/me", "Notion integration").get("type") != "bot":
            raise AndaError("Use a Notion integration token, not a personal token")
        page = get("notion", "/pages/" + notion_parent, "Notion parent page (connect the integration to it)")
        if (str(page.get("id", "")).replace("-", "").lower() != notion_parent.replace("-", "").lower()
                or "public_url" not in page or page["public_url"] is not None or page.get("archived") or page.get("in_trash")):
            raise AndaError("Notion did not confirm the exact active, non-public parent page")
    if guild:
        me = get("discord", "/users/@me", "Discord bot")
        if me.get("bot") is not True or me.get("id") != bot:
            raise AndaError("The Discord token does not belong to the designated bot")
        get("discord", "/guilds/" + guild + "/members/" + bot, "Bot membership (authorize it with the invite link)")
        user = get("discord", "/guilds/" + guild + "/members/" + owner, "Discord owner").get("user")
        if not isinstance(user, dict) or user.get("id") != owner or user.get("bot"):
            raise AndaError("The owner must be a human member of the selected server")

def wizard(ledger, project, discord_app="", guild="", owner="", notion_parent="", notion_user="", claude_project="",
           *, transport=None, ask=None, hidden=None):
    """Chain the existing steps. The user approves and pastes tokens; ANDA never creates apps or integrations."""
    ledger.project(project)
    ask, hidden = ask or input, hidden or _hidden

    def prompt(text, value=""):
        if value:
            return value
        try:
            return ask(text).strip()
        except EOFError:
            raise AndaError("Setup needs your interactive terminal") from None

    def confirm(text):
        return prompt(text + " [y/N] ").lower() in {"y", "yes"}

    def say(*lines):
        print(*lines, sep="\n", flush=True)

    say("ANDA setup · project " + project + " · state " + str(ledger.home),
        "You create the Discord bot and the Notion integration yourself in their official pages.")
    # 1. Discord bot invite; the owner clicks Authorize.
    discord_app = prompt("Discord application ID (= bot user ID), blank to skip Discord: ", discord_app)
    if discord_app:
        guild = prompt("Discord server ID: ", guild)
        owner = prompt("Your Discord user ID: ", owner)
        url = invite_url(discord_app, guild, create_channel=True)
        say("Developer Portal > your app > Bot: turn on Message Content Intent. Copy the token only when asked below.",
            "Open this invite and click Authorize yourself. Manage Channels is only for creating the private",
            "ANDA channel; remove it from the bot's role afterwards (view/send/history stay on the channel).", "  " + url)
        prompt("Press Enter after authorizing the bot in that server ")
    else:
        guild = owner = ""
    notion_parent = notion_page_id(prompt("Notion parent page ID or link, blank to skip Notion: ", notion_parent))
    if notion_parent:
        say("Notion: an internal integration with read + insert content, connected to that page (page ... > Connections).",
            "  https://www.notion.so/profile/integrations")
    # The offline plan validates every ID before any token prompt or network read.
    plan = provision(ledger, project, notion_parent, guild, owner, notion_user)

    # 2. Tokens: protected environment, then the OS store, then hidden input. Never argv, files or output.
    services = [name for name, selected in (("notion", notion_parent), ("discord", guild)) if selected]
    sources, fresh = {}, {}
    for service in services:
        name = ledger.config["destinations"][service]["token_env"]
        if os.environ.get(name):
            sources[name] = "environment"
            continue
        stored = credentials.read(name)
        if stored and not confirm(name + " is already in the OS credential store. Replace it?"):
            os.environ[name], sources[name] = stored, credentials.label()
            continue
        value = hidden(name + " (hidden; paste, then Enter): ").strip()
        if not credentials.TOKEN.fullmatch(value):
            raise AndaError(name + ": unexpected token format; nothing was stored")
        os.environ[name] = fresh[name] = value  # this process only, so redaction covers it

    # 3. Read checks, then store, then the provision plan; --apply only on an explicit yes.
    cfg = copy.deepcopy(ledger.config)
    for service in services:
        cfg["destinations"][service]["enabled"] = True
    api = Connectors(cfg, transport)
    _read_checks(api, notion_parent, guild, owner, discord_app)
    say("Read checks passed (" + ", ".join(services) + "). Reads do not prove write access.")
    doctor = None
    if any(spec.get("enabled") for spec in ledger.config["destinations"].values()):
        doctor = Connectors(ledger.config, transport).doctor()
        say("doctor --live: " + dumps(doctor))
        if any(item["state"] not in {"disabled", "read_access_confirmed"} for item in doctor.values()):
            raise AndaError("doctor --live failed for a configured destination; repair it first")
    for name, value in fresh.items():
        where = credentials.write(name, value)
        sources[name] = where or "this_process_only"
        say(name + (": saved in " + where if where else
                    ": no supported OS credential store here; set it in the worker's protected environment"))
    say(json.dumps(plan, ensure_ascii=False, indent=2))
    provisioned = confirm("Create these NEW resources now (provision --apply)?")
    if provisioned:
        say(json.dumps(provision(ledger, project, notion_parent, guild, owner, notion_user, apply=True, connectors=api),
                       ensure_ascii=False, indent=2))
        doctor = Connectors(ledger.config, transport).doctor()
        say("doctor --live: " + dumps(doctor))

    # 4. Project-local Claude Code config; written only on an explicit yes.
    hooks, server = claude_settings(ledger, project)
    say("Claude Code hooks (<project>/.claude/settings.local.json):", json.dumps({"hooks": hooks}, ensure_ascii=False, indent=2),
        "Claude Code MCP server (<project>/.mcp.json):", json.dumps({"mcpServers": {"anda": server}}, ensure_ascii=False, indent=2))
    folder = prompt("Claude Code project folder to write these into, blank to skip: ", claude_project)
    written = []
    if folder and confirm("Write them under " + folder + "? Global Claude settings are never touched."):
        written = write_claude_project(folder, hooks, server, ledger.home)
        say("Claude Code asks you to approve the ANDA MCP server and to trust the hooks on its next start.",
            "Keep .mcp.json out of version control: it holds local paths.")

    # 5. Verification is separate from setup.
    root = Path(__file__).resolve().parents[2] / "scripts"
    return {"state": "configured" if provisioned else "checked_not_applied", "credential_sources": sources,
            "provisioned": provisioned, "doctor": doctor, "claude_files_written": written,
            "next": [str(root / "verify_claude_hooks.py") + " --home <NEW empty folder>",
                     str(root / "anda.py") + " --home " + str(ledger.home) + " doctor --live"]}
