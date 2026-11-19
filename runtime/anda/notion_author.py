"""Owner-only Notion author binding without workspace user lookup."""
import hashlib
import re
from uuid import UUID

from .core import AndaError

ENROLLMENT_PREFIX = "ANDA-AUTHOR-PROOF-"
METHOD = "private-owner-nonce-v1"


def native_id(value):
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def same_page(block, page):
    parent = block.get("parent", {})
    return parent.get("type") == "page_id" and native_id(parent.get("page_id")) == native_id(page) is not None


def paragraph_text(block):
    rich = block.get("paragraph", {}).get("rich_text", [])
    return "".join(item.get("plain_text", "") for item in rich if item.get("type") == "text")


def authors(block):
    return tuple(native_id(block.get(field, {}).get("id")) for field in ("created_by", "last_edited_by"))


def validate_proof(spec):
    proof = spec.get("author_proof")
    names = {"method", "author", "page", "bot", "block", "challenge_sha256"}
    if (not isinstance(proof, dict) or set(proof) != names or proof["method"] != METHOD
            or any(not native_id(proof[field]) or proof[field] != native_id(proof[field])
                   for field in ("author", "page", "bot", "block"))
            or proof["author"] == proof["bot"] or proof["page"] != native_id(spec.get("page_id"))
            or spec.get("user_ids") != [proof["author"]]
            or not isinstance(proof["challenge_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", proof["challenge_sha256"])):
        raise AndaError("Notion author proof missing or outside the exact approved scope")
    return proof


def enrollment(page, bot, challenge, baseline_ids, blocks):
    """Bind a challenge disclosed only in the trusted owner's local console."""
    if not re.fullmatch(ENROLLMENT_PREFIX + r"[0-9a-f]{32}", challenge):
        raise AndaError("Invalid Notion owner challenge")
    matches = [block for block in blocks if block.get("type") == "paragraph"
               and not block.get("archived") and not block.get("in_trash")
               and paragraph_text(block) == challenge]
    if len(matches) != 1:
        raise AndaError("Missing or ambiguous Notion owner challenge")
    block = matches[0]
    block_id = native_id(block.get("id"))
    creator, editor = authors(block)
    bot_id = native_id(bot)
    if (not block_id or block_id in baseline_ids or not bot_id or not same_page(block, page)
            or not creator or creator != editor or creator == bot_id):
        raise AndaError("Notion challenge is stale, out of scope or has conflicting authors")
    return {"method": METHOD, "author": creator, "page": native_id(page), "bot": bot_id,
            "block": block_id, "challenge_sha256": hashlib.sha256(challenge.encode()).hexdigest()}


def eligible(block, text, proof):
    creator, editor = authors(block)
    return (native_id(block.get("id")) != proof["block"] and not text.startswith(ENROLLMENT_PREFIX)
            and creator == editor == proof["author"] and creator != proof["bot"])
