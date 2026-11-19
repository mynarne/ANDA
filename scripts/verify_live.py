"""Verify live ANDA receipts for one synthetic request, without sending messages."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from anda.connectors import Connectors, DeliveryError
from anda.core import AndaError, Ledger


def equal_id(left, right):
    return isinstance(left, str) and left.replace("-", "") == right.replace("-", "")


def rich_text(items):
    return "".join(item.get("plain_text", item.get("text", {}).get("content", "")) for item in items)


def require(condition, message):
    if not condition:
        raise AndaError(message)


def verify_discord_request(api, job):
    channel = api.config["destinations"]["discord"]["channel_id"]
    intake = api.config["intake"]["discord"]
    source_id = job["source_id"]
    require(intake["enabled"] and job["conversation"] == channel,
            "Stored Discord request is outside the configured intake channel")
    require(bool(re.fullmatch(r"[0-9]{5,25}", source_id)), "Invalid original Discord message ID")
    message = api.call("discord", "GET", "/channels/" + channel + "/messages/" + source_id)
    author = message.get("author", {})
    matches = (
        message.get("id") == source_id
        and message.get("channel_id") == channel
        and author.get("id") in intake["owner_ids"]
        and not author.get("bot")
        and not message.get("webhook_id")
        and message.get("content") == intake["prefix"] + job["request_text"]
    )
    require(matches, "Original Discord message did not match the accepted ANDA request")
    return source_id


def verify_delivery(api, destination, delivery, response):
    target, receipt = delivery["target_id"], delivery["receipt"]
    marker = api.marker(delivery)
    api.check_target(delivery)
    if destination == "notion":
        require(bool(re.fullmatch(r"[0-9a-fA-F-]{32,36}", receipt)), "Invalid Notion receipt")
        actor = api.call("notion", "GET", "/users/me")
        page = api.call("notion", "GET", "/pages/" + receipt)
        blocks = api.pages("notion", "/blocks/" + receipt + "/children?page_size=100")
        visible = "".join(rich_text(b["paragraph"]["rich_text"]) for b in blocks if b["type"] == "paragraph")
        matching = (
            actor.get("type") == "bot"
            and equal_id(page.get("id"), receipt)
            and equal_id(page.get("parent", {}).get("page_id"), target)
            and page.get("created_by", {}).get("id") == actor.get("id")
            and not page.get("public_url")
            and api.notion_title(response, delivery) == rich_text(page["properties"]["title"]["title"])
            and "\nEvent: " + response["id"] + "\n" in visible
            and visible.endswith("\n\n" + response["text"])
        )
    else:
        require(bool(re.fullmatch(r"[0-9]{5,25}", receipt)), "Invalid Discord receipt")
        actor = api.call("discord", "GET", "/users/@me")
        message = api.call("discord", "GET", "/channels/" + target + "/messages/" + receipt)
        visible = message.get("content", "")
        matching = (
            actor.get("bot")
            and message.get("id") == receipt
            and message.get("channel_id") == target
            and message.get("author", {}).get("id") == actor.get("id")
            and "\n" + response["text"][:800] + "\n—\n" + marker + "\n" in visible
        )
    require(matching, "Live receipt did not match the configured " + destination + " destination and response")
    return receipt


def verify_mcp(home, project, marker, response):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "acceptance-verifier", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory_context", "arguments": {"query": marker}}},
    ]
    output = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "anda.py"), "--home", str(home), "mcp",
         "--project", project, "--agent", "acceptance-verifier"],
        input="".join(json.dumps(item) + "\n" for item in requests), text=True,
        capture_output=True, check=True, timeout=20,
    )
    replies = [json.loads(line) for line in output.stdout.splitlines()]
    lookup = next(item["result"] for item in replies if item["id"] == 2)
    context = json.loads(lookup["content"][0]["text"])
    return context["project"] == project and any(
        item["id"] == response["id"] and item["text"] == response["text"]
        for item in context["events"]
    )


def verify(ledger, project, source, marker):
    ledger.project(project)
    rows = ledger.db.execute(
        "SELECT e.id AS request_id,e.source_id,e.conversation,e.text AS request_text,j.state,j.result_id,j.provider FROM events e "
        "JOIN jobs j ON e.id=j.event_id WHERE e.project=? AND e.source=? AND e.kind='request' "
        "AND instr(e.text,?)>0 LIMIT 2",
        (project, source, marker),
    ).fetchall()
    require(len(rows) == 1, "Expected one accepted request with this marker and source")
    job = rows[0]
    require(job["state"] == "completed", "Model job is not complete: " + job["state"])
    response = dict(ledger.db.execute("SELECT * FROM events WHERE id=?", (job["result_id"],)).fetchone())
    require(response["kind"] == "response" and response["project"] == project, "Stored response scope mismatch")
    require(len(response["text"].encode()) <= 800 and response["text"].strip() == marker,
            "Expected the exact synthetic test marker as the short response")
    api = Connectors(ledger.config)
    original_message = verify_discord_request(api, job) if source == "discord" else None
    receipts = {}
    rows = ledger.db.execute("SELECT * FROM outbox WHERE event_id=?", (response["id"],)).fetchall()
    for row in rows:
        destination = row["destination"]
        require(row["state"] == "confirmed" and row["receipt"], "Delivery is not confirmed: " + destination)
        receipts[destination] = verify_delivery(api, destination, dict(row), response)
    require(set(receipts) == {"notion", "discord"}, "Both destination receipts are required")
    require(verify_mcp(ledger.home, project, marker, response), "Independent MCP process did not read the exact response")
    return {"state": "live_receipts_and_mcp_verified", "request_event_id": job["request_id"],
            "response_event_id": response["id"], "request_source": source, "provider": job["provider"],
            "discord_original_message_id": original_message, "receipts": receipts,
            "overall_verified": False, "second_native_ai": "requires_observed_client_test"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source", required=True, choices=["notion", "discord", "operator"])
    parser.add_argument("--marker", required=True)
    parser.add_argument("--verify-second-claude", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"ANDA-LIVE-[0-9a-f]{12}", args.marker):
        parser.error("Use ANDA-LIVE- followed by 12 lowercase hexadecimal characters")
    ledger = None
    try:
        ledger = Ledger(args.home)
        result = verify(ledger, args.project, args.source, args.marker)
        if args.verify_second_claude:
            from claude_readback import readback
            require(args.source == "discord" and result["provider"] == "codex",
                    "Full test requires Discord ingress and a Codex first response")
            try:
                result["second_native_ai"] = readback(
                    ROOT, ledger.home, args.project, args.marker, result["response_event_id"])
            except AndaError as exc:
                result["second_native_ai"] = {"state": "not_verified", "reason": str(exc)}
                print(json.dumps(result, ensure_ascii=False))
                return 2
            result["state"] = "verified"
            result["overall_verified"] = True
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (AndaError, DeliveryError) as exc:
        print(json.dumps({"state": "not_verified", "reason": str(exc)}))
        return 2
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError, StopIteration):
        print(json.dumps({"state": "not_verified", "reason": "Unexpected receipt, configuration, or MCP response"}))
        return 2
    finally:
        if ledger:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
