"""Bounded read-only synchronization for an exact synthetic Notion paragraph."""
import time

from .connectors import DeliveryError
from .notion_author import eligible, paragraph_text, same_page


class Stop(Exception):
    pass


def await_note(read, marker, baseline, page, proof, sleep=time.sleep):
    for attempt in range(5):
        try:
            blocks = read()
        except DeliveryError as exc:
            if exc.state == "blocked" or str(exc).endswith("HTTP 404"):
                raise Stop("notion_access_denied") from None
            if exc.state not in {"pending", "unknown"}:
                raise Stop("note_intake_failed") from None
            if attempt == 4 or exc.delay > 20:
                raise Stop("notion_transient_error") from None
            sleep(max(exc.delay, 2 ** attempt))
            continue
        matches = [b for b in blocks if b.get("type") == "paragraph" and paragraph_text(b) == marker]
        if len(matches) > 1:
            raise Stop("note_ambiguous")
        if matches:
            block = matches[0]
            if (not block.get("id") or block["id"] in baseline or not same_page(block, page)
                    or block.get("archived") or block.get("in_trash")):
                raise Stop("note_scope_mismatch")
            if not eligible(block, marker, proof):
                raise Stop("note_author_mismatch")
            return block
        if attempt < 4:
            sleep(2 ** attempt)
    for block in blocks:
        kind = block.get("type")
        if not isinstance(kind, str) or not isinstance(block.get(kind), dict):
            continue
        rich = block[kind].get("rich_text", [])
        visible = "".join(item.get("plain_text", "") for item in rich if item.get("type") == "text")
        if visible.strip() == marker:
            raise Stop("note_text_mismatch" if kind == "paragraph" else "note_not_direct_paragraph")
    raise Stop("note_not_visible")
