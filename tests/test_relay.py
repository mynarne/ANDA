import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from anda.cli import capture_hook
from anda.connectors import Connectors, DeliveryError
from anda.core import AndaError, Ledger, example_config
from anda.engine import deliver_one, pull_discord, pull_notion, work_job
from anda.relay import setup_status

CHANNEL = "10001"
BOT = "10002"
HUMAN = "10003"
GUILD = "10004"
PAGE = "00000000-0000-4000-8000-000000000001"
PERSON = "00000000-0000-4000-8000-000000000002"
NOTION_BOT = "00000000-0000-4000-8000-000000000004"
BLOCK = "00000000-0000-4000-8000-000000000005"
PROOF_BLOCK = "00000000-0000-4000-8000-000000000006"


def consent(*destinations):
    return {"consent": True, "visibility": "private", "destinations": list(destinations)}


class SyntheticGateway:
    def __init__(self):
        self.messages = []
        self.blocks = []
        self.sent = []
        self.role_view = False

    def call(self, name, method, path):
        assert method == "GET"
        if name == "discord":
            if path == "/users/@me":
                return {"id": BOT, "bot": True}
            if path == "/channels/" + CHANNEL:
                return {"id": CHANNEL, "type": 0, "guild_id": GUILD, "permission_overwrites": [
                    {"id": GUILD, "type": 0, "allow": "0", "deny": "1024"},
                    {"id": "10005", "type": 0, "allow": "1024" if self.role_view else "0", "deny": "0"},
                ]}
            if path.startswith("/channels/" + CHANNEL + "/messages?"):
                return self.messages
        if name == "notion":
            if path == "/pages/" + PAGE:
                return {"id": PAGE, "public_url": None}
            if path == "/users/me":
                return {"id": NOTION_BOT, "type": "bot"}
        raise AssertionError("Unexpected request")

    def pages(self, name, path):
        assert name == "notion" and path == "/blocks/" + PAGE + "/children?page_size=100"
        return self.blocks

    def send(self, event, delivery):
        self.sent.append((event, delivery))
        return "synthetic-receipt"


def message(index, content, **fields):
    return {"id": str(20000 + index), "channel_id": CHANNEL, "type": 0,
            "author": {"id": HUMAN}, "content": content, **fields}


def block(text, minute=1, actor=PERSON):
    return {"id": BLOCK, "type": "paragraph",
            "last_edited_time": f"2026-01-01T00:{minute:02}:00Z",
            "parent": {"type": "page_id", "page_id": PAGE},
            "created_by": {"id": actor}, "last_edited_by": {"id": actor},
            "paragraph": {"rich_text": [{"type": "text", "plain_text": text}]}}


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "state"
        self.config = example_config()
        self.config["projects"].append("other")
        self.config["destinations"]["discord"].update(enabled=True, channel_id=CHANNEL)
        self.config["destinations"]["notion"].update(enabled=True, parent_page_id=PAGE)
        self.config["intake"]["discord"].update(enabled=True, owner_ids=[HUMAN], bot_id=BOT)
        self.config["intake"]["notion"].update(enabled=True, page_id=PAGE, user_ids=[PERSON],
            author_proof={"method": "private-owner-nonce-v1", "page": PAGE, "bot": NOTION_BOT,
                          "author": PERSON, "block": PROOF_BLOCK, "challenge_sha256": "0" * 64})
        self.config["provider"].update(enabled=True, max_calls_per_day=1)
        self.config["relay"] = {"paused": False, "projects": {"demo": {
            "routes": {
                "discord.input": consent("notion"), "discord.response": consent("discord", "notion"),
                "notion.note": consent("discord"), "hook.user": consent("notion"),
                "hook.assistant": consent("discord", "notion"),
            },
            "hooks": {name: {"cwd": self.temp.name, "sessions": ["synthetic-session"]}
                      for name in ("codex", "claude", "controlled")},
        }}}
        self.gateway = SyntheticGateway()
        self.ledger = Ledger(self.home, self.config)

    def tearDown(self):
        self.ledger.close()

    def reopen(self):
        self.ledger.close()
        self.ledger = Ledger(self.home, self.config)

    def test_prefix_jobs_budget_and_frozen_deliveries_across_reconnect(self):
        self.gateway.messages = [message(1, "!anda historical")]
        self.assertEqual(pull_discord(self.ledger, self.gateway)["state"], "baseline")
        self.gateway.messages += [message(7, "!anda second"), message(2, "ordinary"),
                                  message(3, "!anda webhook", webhook_id="synthetic"),
                                  message(4, "!anda sent by own integration"), message(5, "!anda approved"),
                                  message(6, "!anda foreign")]
        self.gateway.messages[-1]["author"] = {"id": "10006"}
        self.gateway.messages[-3]["author"] = {"id": BOT, "bot": True}
        self.assertEqual(pull_discord(self.ledger, self.gateway)["count"], 2)
        self.reopen()
        self.assertEqual(pull_discord(self.ledger, self.gateway)["count"], 0)
        calls = []
        def provider(prompt, home, timeout):
            calls.append(prompt)
            return "synthetic visible answer"
        work_job(self.ledger, provider)
        self.assertIsNone(work_job(self.ledger, provider))
        self.assertEqual(len(calls), 1)
        self.assertIn("CURRENT_USER_REQUEST:\napproved", calls[0])
        self.assertNotIn(CHANNEL, calls[0])
        self.assertNotIn(PAGE, calls[0])
        self.assertNotIn("second", calls[0])
        first = self.ledger.status()["deliveries"][0]
        self.config["destinations"]["notion"]["parent_page_id"] = "00000000-0000-4000-8000-000000000003"
        self.assertEqual(deliver_one(self.ledger, self.gateway), first["id"])
        self.assertEqual(self.ledger.status()["deliveries"][0]["state"], "blocked")
        self.assertEqual(self.gateway.sent, [])

    def test_all_text_requires_private_channel_and_skips_sensitive_or_mirrored_input(self):
        self.config["intake"]["discord"]["mode"] = "all_text"
        self.assertEqual(pull_discord(self.ledger, self.gateway)["state"], "baseline")
        self.gateway.messages = [message(1, "approved text"), message(2, "bot_token=opaque"),
                                 message(3, "ANDA:" + "0" * 36), message(4, "known secret value")]
        self.gateway.role_view = True
        with self.assertRaises(AndaError):
            pull_discord(self.ledger, self.gateway)
        self.gateway.role_view = False
        with patch.dict("os.environ", {"ANDA_TEST_TOKEN": "known secret value"}):
            self.assertEqual(pull_discord(self.ledger, self.gateway)["count"], 1)
        self.assertEqual(self.ledger.changes("demo")["events"][0]["text"], "approved text")
        self.assertNotIn("opaque", repr(self.ledger.changes("demo")))

    def test_notion_revision_only_and_read_only_mcp(self):
        self.gateway.blocks = [block("old")]
        self.assertEqual(pull_notion(self.ledger, self.gateway)["state"], "baseline")
        self.gateway.blocks = [block("first edit", 2)]
        self.assertEqual(pull_notion(self.ledger, self.gateway)["count"], 1)
        self.reopen()
        self.gateway.blocks = [block("first edit", 3)]
        self.assertEqual(pull_notion(self.ledger, self.gateway)["count"], 0)
        self.gateway.blocks = [block("foreign edit", 4, NOTION_BOT)]
        self.assertEqual(pull_notion(self.ledger, self.gateway)["count"], 0)
        self.gateway.blocks = [block("second edit", 5)]
        self.assertEqual(pull_notion(self.ledger, self.gateway)["count"], 1)
        self.assertEqual(self.ledger.status()["jobs"], [])
        self.assertEqual({item["destination"] for item in self.ledger.status()["deliveries"]}, {"discord"})
        (self.home / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        (self.home / "config.json").chmod(0o600)
        messages = [
            {"id": 1, "method": "tools/list"},
            {"id": 2, "method": "tools/call", "params": {"name": "relay_changes", "arguments": {"limit": 1}}},
            {"id": 3, "method": "tools/call", "params": {"name": "relay_changes", "arguments": {"after": 1}}},
            {"id": 4, "method": "tools/call", "params": {"name": "memory_record", "arguments": {}}},
        ]
        out = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "anda.py"), "--home", str(self.home),
                              "mcp", "--project", "demo"], check=True, capture_output=True, text=True,
                             input="".join(json.dumps({"jsonrpc": "2.0", **item}) + "\n" for item in messages))
        listed, first, second, write = map(json.loads, out.stdout.splitlines())
        self.assertNotIn("memory_record", {tool["name"] for tool in listed["result"]["tools"]})
        payload = json.loads(first["result"]["content"][0]["text"])
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["events"][0]["text"], "first edit")
        self.assertEqual(json.loads(second["result"]["content"][0]["text"])["events"][0]["text"], "second edit")
        self.assertIn("error", write)
        self.assertEqual(self.ledger.changes("other")["events"], [])

    def test_visible_hook_scope_pause_and_replay(self):
        payload = {"cwd": self.temp.name, "session_id": "synthetic-session", "turn_id": "turn-1",
                   "hook_event_name": "UserPromptSubmit", "prompt": "visible request",
                   "transcript_path": "must-not-read", "reasoning": "must-not-store"}
        self.assertTrue(capture_hook(self.ledger, "demo", "codex", payload)["persisted"])
        self.assertTrue(capture_hook(self.ledger, "demo", "codex", payload)["duplicate"])
        final = {**payload, "hook_event_name": "Stop", "last_assistant_message": "visible answer"}
        self.assertTrue(capture_hook(self.ledger, "demo", "codex", final)["persisted"])
        self.assertFalse(capture_hook(self.ledger, "demo", "codex", {**final, "stop_hook_active": True})["persisted"])
        self.assertFalse(capture_hook(self.ledger, "demo", "claude", final)["persisted"])
        self.assertFalse(capture_hook(self.ledger, "demo", "codex", {**final, "cwd": str(self.home)})["persisted"])
        self.assertTrue(capture_hook(self.ledger, "demo", "controlled", {
            "schema": "anda.event.v1", "cwd": self.temp.name, "conversation_id": "synthetic-session",
            "event_id": "turn-2", "event_type": "assistant_message", "text": "controlled answer",
        })["persisted"])
        self.reopen()
        self.config["relay"]["paused"] = True
        self.assertIsNone(deliver_one(self.ledger, self.gateway))
        self.config["relay"]["paused"] = False
        while deliver_one(self.ledger, self.gateway):
            pass
        self.assertEqual(len(self.gateway.sent), 5)
        self.assertEqual(self.ledger.status()["jobs"], [])
        self.assertNotIn("must-not-store", repr(self.ledger.changes("demo")))

    def test_preflight_and_revoked_route_do_not_write_destination(self):
        before = copy.deepcopy(self.config)
        with patch.object(Connectors, "call", side_effect=AssertionError("Unexpected network")):
            self.assertFalse(setup_status(self.ledger)["network_calls"])
        self.assertEqual(self.config, before)
        connector = Connectors(self.config)
        with patch.object(connector, "call", return_value={"id": PAGE, "public_url": "synthetic"}) as call:
            with self.assertRaises(DeliveryError):
                connector.send({}, {"destination": "notion", "target_id": PAGE, "route": "relay:discord.input"})
            call.assert_called_once_with("notion", "GET", "/pages/" + PAGE)
        self.gateway.messages = [message(1, "baseline")]
        pull_discord(self.ledger, self.gateway)
        self.gateway.messages += [message(2, "!anda next")]
        pull_discord(self.ledger, self.gateway)
        del self.config["relay"]["projects"]["demo"]["routes"]["discord.input"]
        self.assertIsNone(work_job(self.ledger, lambda *_: self.fail("revoked job ran")))
        deliver_one(self.ledger, self.gateway)
        self.assertEqual(self.gateway.sent, [])

    def test_end_to_end_reopen_and_no_ambiguous_post_replay(self):
        pull_discord(self.ledger, self.gateway)
        self.gateway.messages = [message(1, "!anda synthetic request")]
        self.assertEqual(pull_discord(self.ledger, self.gateway)["count"], 1)
        self.reopen()
        work_job(self.ledger, lambda *_: "synthetic response")
        self.reopen()
        self.assertEqual(pull_discord(self.ledger, self.gateway)["count"], 0)
        with patch.object(self.gateway, "send", side_effect=DeliveryError("unknown", "Synthetic timeout")) as send:
            deliver_one(self.ledger, self.gateway)
            self.assertEqual(send.call_count, 1)
        self.reopen()
        while deliver_one(self.ledger, self.gateway):
            pass
        self.assertEqual(len(self.gateway.sent), 2)
        self.assertEqual({d["destination"] for _, d in self.gateway.sent}, {"notion", "discord"})
        self.assertTrue(all(e["text"] == "synthetic response" for e, _ in self.gateway.sent))
        self.assertEqual([d["state"] for d in self.ledger.status()["deliveries"]], ["unknown", "confirmed", "confirmed"])
        self.assertEqual(len(self.ledger.status()["jobs"]), 1)

    def test_queued_discord_delivery_cannot_follow_changed_audience(self):
        self.gateway.blocks = [block("old")]
        pull_notion(self.ledger, self.gateway)
        self.gateway.blocks = [block("eligible edit", 2)]
        self.assertEqual(pull_notion(self.ledger, self.gateway)["count"], 1)
        self.config["intake"]["discord"]["owner_ids"].append("10007")
        self.reopen()
        deliver_one(self.ledger, self.gateway)
        self.assertEqual(self.gateway.sent, [])
        self.assertEqual(self.ledger.status()["deliveries"][0]["state"], "blocked")


    def test_owner_proof_replay_and_second_actor_fail_closed(self):
        from anda.notion_author import enrollment
        nonce = "ANDA-AUTHOR-PROOF-" + "a" * 32
        original = block(nonce)
        proof = enrollment(PAGE, NOTION_BOT, nonce, set(), [original])
        self.assertEqual(proof["author"], PERSON)
        with self.assertRaises(AndaError):
            enrollment(PAGE, NOTION_BOT, nonce, {BLOCK}, [original])
        other = {**original, "id": PROOF_BLOCK, "created_by": {"id": NOTION_BOT}}
        with self.assertRaises(AndaError):
            enrollment(PAGE, NOTION_BOT, nonce, set(), [original, other])
        with self.assertRaises(AndaError):
            enrollment(PAGE, NOTION_BOT, nonce, set(), [other])
        pull_notion(self.ledger, self.gateway)
        second = block("unapproved actor")
        second["last_edited_by"] = {"id": "00000000-0000-4000-8000-000000000007"}
        self.gateway.blocks = [second]
        self.assertEqual(pull_notion(self.ledger, self.gateway)["count"], 0)
        self.gateway.blocks = [block(nonce, 2)]
        self.assertEqual(pull_notion(self.ledger, self.gateway)["count"], 0)
        del self.config["intake"]["notion"]["author_proof"]
        with self.assertRaises(AndaError):
            pull_notion(self.ledger, self.gateway)
        self.assertEqual(self.ledger.status()["deliveries"], [])
