"""Opt-in provisioning of NEW resources only; durable intent before each POST."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import shlex
import tempfile
from .core import AndaError, dumps, validate_config
from .connectors import Connectors, DeliveryError

def atomic_config(home, config):
    validate_config(config)
    path = Path(home) / "config.json"
    if path.is_symlink():
        raise AndaError("Config symlink rejected")
    fd, temp = tempfile.mkstemp(prefix=".config-", dir=home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps(config) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

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
