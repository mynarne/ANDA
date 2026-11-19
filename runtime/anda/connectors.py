"""Fixed-origin REST connectors. POST ambiguity is preserved, never blindly retried."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from .core import AndaError, dumps

class DeliveryError(AndaError):
    def __init__(self, state, message, delay=0, receipt=None):
        super().__init__(message)
        self.state, self.delay, self.receipt = state, delay, receipt

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Transport:
    def request(self, service, method, path, token, body=None):
        base = {"notion": "https://api.notion.com/v1", "discord": "https://discord.com/api/v10"}[service]
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise AndaError("Invalid fixed-origin API path")
        headers = {"Authorization": ("Bearer " if service == "notion" else "Bot ") + token,
                   "Content-Type": "application/json", "User-Agent": "ANDA/0.3"}
        if service == "notion":
            headers["Notion-Version"] = "2025-09-03"
        req = urllib.request.Request(base + path, method=method, headers=headers,
                                     data=dumps(body).encode("utf-8") if body is not None else None)
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=15) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise DeliveryError("unknown" if method == "POST" else "failed", "API response too large")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read(200000)
            try:
                info = json.loads(raw)
                if not isinstance(info, dict): info = {}
            except (ValueError, UnicodeError):
                info = {}
            if exc.code in (401, 403):
                raise DeliveryError("blocked", "Authentication or permission rejected") from None
            if exc.code == 429 or (service == "notion" and exc.code == 529):
                # Notion documents 529 as safe to retry. Discord 429 also carries retry_after.
                retry = exc.headers.get("Retry-After", info.get("retry_after", 30))
                try:
                    retry = max(1, min(float(retry), 86400))
                except (ValueError, TypeError):
                    retry = 30
                raise DeliveryError("pending", "Rate limited", retry) from None
            if exc.code == 503 and service == "notion":
                data = info.get("additional_data", {})
                if not isinstance(data, dict): data = {}
                receipt = data.get("committed_resource_id")
                raise DeliveryError("unknown", "Notion write outcome requires reconciliation", receipt=receipt) from None
            if 300 <= exc.code < 400:
                raise DeliveryError("blocked", "API redirect rejected") from None
            if 400 <= exc.code < 500:
                raise DeliveryError("failed", "API rejected request: HTTP " + str(exc.code)) from None
            raise DeliveryError("unknown" if method == "POST" else "pending", "API temporarily unavailable", 30) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise DeliveryError("unknown" if method == "POST" else "pending", "Transport response unavailable", 30) from None

class Connectors:
    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or Transport()
        self._bot_id = None
        self._notion_bot_id = None

    def call(self, name, method, path, body=None):
        spec = self.config["destinations"][name]
        if not spec.get("enabled"):
            raise DeliveryError("blocked", "Connector is disabled")
        token = os.environ.get(spec["token_env"])
        if not token:
            raise DeliveryError("blocked", "Required connector secret is not set")
        return self.transport.request(name, method, path, token, body)

    def record_text(self,event):
        from .formatting import kst
        if event["source"]=="memory":
            try:
                memory=json.loads(event["text"])
                if memory.get("record_type")=="memory_revision":
                    return ("메모리 · "+memory["kind"]+" · "+memory["key"]+
                            "\n기록 당시 상태: "+memory["status"]+" · "+memory["authority"]+
                            "\n적용 범위: "+memory["scope"]+"\n기록: "+kst(memory["created"])+
                            "\n유효 시작: "+kst(memory["effective"])+"\n만료: "+str(kst(memory["expires"]))+
                            "\n출처: "+memory["source"]+" · "+memory["source_event_id"]+
                            "\nRevision: "+memory["revision"]+"\n이전 revision: "+str(memory["previous_revision"])+
                            "\n최신값·만료 여부는 MCP 메모리 조회를 기준으로 확인합니다.\n\n"+memory["text"])
            except (ValueError,KeyError,AttributeError,TypeError):
                pass
        return event["text"]

    def marker(self, delivery):
        return "ANDA:" + delivery["id"]

    def notion_title(self, event, delivery):
        return "[" + self.marker(delivery) + "] " + event["project"] + " · " + event["kind"]

    def check_target(self, delivery):
        name = delivery["destination"]
        spec = self.config["destinations"][name]
        key = "memory_page_id" if name == "notion" and delivery.get("route")=="memory" else "parent_page_id" if name=="notion" else "channel_id"
        target = spec.get(key)
        if not spec.get("enabled") or delivery.get("target_id") != target:
            raise DeliveryError("blocked", "Destination scope changed; queued records will not be rerouted")

    def send(self, event, delivery):
        self.check_target(delivery)
        name = delivery["destination"]
        if delivery.get("route", "").startswith("relay:"):
            from .relay import discord_scope
            target = delivery["target_id"]
            try:
                if name == "discord":
                    intake = self.config["intake"]["discord"]
                    discord_scope(self, target, intake["owner_ids"], intake["bot_id"])
                else:
                    page = self.call("notion", "GET", "/pages/" + target)
                    if (str(page.get("id", "")).replace("-", "") != target.replace("-", "")
                            or "public_url" not in page or page["public_url"] is not None
                            or page.get("archived") or page.get("in_trash")):
                        raise AndaError("Notion destination must be the exact active non-public page")
            except DeliveryError:
                raise
            except (AndaError, AttributeError, TypeError, ValueError, KeyError):
                raise DeliveryError("blocked", "Relay destination scope could not be verified") from None
        marker = self.marker(delivery)
        if name == "notion":
            spec = self.config["destinations"]["notion"]
            text = ("Project: " + event["project"] + "\nSource: " + event["source"] + "/" + event["conversation"] +
                    "\nRole/kind: " + event["role"] + "/" + event["kind"] +
                    "\nEvent: " + event["id"] + "\nRecorded: " + __import__("anda.formatting",fromlist=["kst"]).kst(event["created"]) + "\nVerification: recorded, not independently verified\n\n" + self.record_text(event))
            children = [{"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": text[i:i+800]}}]}} for i in range(0, len(text), 800)]
            body = {"parent": {"page_id": delivery["target_id"]},
                    "properties": {"title": {"type": "title", "title": [{"type": "text", "text": {"content": self.notion_title(event, delivery)}}]}},
                    "children": children}
            result = self.call(name, "POST", "/pages", body)
        else:
            channel = self.config["destinations"]["discord"]["channel_id"]
            body = {"content": "**" + event["project"] + " · " + event["kind"] + "**\n" +
                               self.record_text(event)[:800] + "\n—\n" + marker +
                               "\n기록 저장 · 구현/검증 완료 여부는 원문 근거 확인",
                    "allowed_mentions": {"parse": []}, "nonce": delivery["id"].replace("-", "")[:24], "enforce_nonce": True}
            result = self.call(name, "POST", "/channels/" + channel + "/messages", body)
        receipt = result.get("id")
        if not isinstance(receipt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,100}", receipt):
            raise DeliveryError("unknown", "No valid external receipt returned")
        return receipt

    def pages(self, name, path, max_pages=10):
        values = []
        for _ in range(max_pages):
            response = self.call(name, "GET", path)
            values.extend(response.get("results", []))
            if not response.get("has_more"):
                return values
            cursor = response.get("next_cursor")
            if not isinstance(cursor, str):
                raise DeliveryError("failed", "Pagination cursor missing")
            join = "&" if "?" in path else "?"
            path = path.split("&start_cursor=")[0] + join + "start_cursor=" + urllib.parse.quote(cursor, safe="")
        raise DeliveryError("blocked", "Pagination bound reached; no negative conclusion is safe")

    def reconcile(self, event, delivery):
        """Positive matches only. Absence is not proof a timed-out POST never committed."""
        self.check_target(delivery)
        if delivery["destination"] == "notion":
            if self._notion_bot_id is None:
                me = self.call("notion", "GET", "/users/me")
                if me.get("type") != "bot" or not me.get("id"):
                    raise DeliveryError("blocked", "Expected a Notion connection bot identity")
                self._notion_bot_id = me["id"]
            parent = delivery["target_id"]
            for block in self.pages("notion", "/blocks/" + parent + "/children?page_size=100"):
                if block.get("type") == "child_page" and block.get("created_by", {}).get("id") == self._notion_bot_id and block.get("child_page", {}).get("title") == self.notion_title(event, delivery):
                    return block["id"]
        else:
            channel = self.config["destinations"]["discord"]["channel_id"]
            if self._bot_id is None:
                me = self.call("discord", "GET", "/users/@me")
                if not me.get("bot") or not me.get("id"):
                    raise DeliveryError("blocked", "Expected an authenticated bot account")
                self._bot_id = me["id"]
            path = "/channels/" + channel + "/messages?limit=100"
            for _ in range(10):
                messages = self.call("discord", "GET", path)
                if not isinstance(messages, list):
                    raise DeliveryError("failed", "Invalid Discord history response")
                for msg in messages:
                    if msg.get("author", {}).get("id") == self._bot_id and self.marker(delivery) in msg.get("content", ""):
                        return msg["id"]
                if len(messages) < 100:
                    break
                path = "/channels/" + channel + "/messages?limit=100&before=" + str(min(int(m["id"]) for m in messages))
        return None

    def doctor(self):
        results = {}
        for name, spec in self.config["destinations"].items():
            if not spec.get("enabled"):
                results[name] = {"state": "disabled"}
                continue
            path = "/pages/" + spec["parent_page_id"] if name == "notion" else "/channels/" + spec["channel_id"]
            try:
                response = self.call(name, "GET", path)
            except DeliveryError as exc:
                results[name] = {"state": exc.state, "reason": str(exc), "write_access": "not_tested"}
                continue
            expected = spec["parent_page_id"] if name == "notion" else spec["channel_id"]
            actual = response.get("id") if isinstance(response, dict) else None
            if not isinstance(actual, str) or actual.replace("-", "") != expected.replace("-", ""):
                results[name] = {"state": "failed", "reason": "Unexpected destination identity", "write_access": "not_tested"}
                continue
            results[name] = {"state": "read_access_confirmed", "id": actual, "write_access": "not_tested"}
        return results
