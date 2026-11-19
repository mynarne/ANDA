import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

file = Path(__file__).resolve().parent.parent / "scripts" / "onboard.py"
spec = importlib.util.spec_from_file_location("anda_onboard_t", file)
onboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(onboard)
PAGE = "00000000-0000-4000-8000-000000000001"
GUILD, CHAN, BOT, OWNER, OTHER = [str(10**17 + i) for i in range(5)]
TOKENS = {"ANDA_NOTION_TOKEN": "notion-fixture-only", "ANDA_DISCORD_TOKEN": "discord-fixture-only"}


def overwrite(ident, kind, a=0, d=0):
    return {"id": ident, "type": kind, "allow": str(a), "deny": str(d)}


class Fixture:
    def __init__(self):
        self.calls = []
        self.notion_bot = {"object": "user", "type": "bot"}
        self.page = {"id": PAGE, "object": "page", "archived": False, "public_url": None}
        self.bot = {"id": BOT, "bot": True}
        self.guild = {"id": GUILD, "owner_id": OWNER}
        self.owner = {"user": {"id": OWNER, "username": "expected.user"}}
        self.channel = {"id": CHAN, "type": 0, "guild_id": GUILD, "permission_overwrites": [
            overwrite(GUILD, 0, d=onboard.VIEW),
            overwrite(BOT, 1, a=onboard.BOT_REQUIRED),
            overwrite(OWNER, 1, a=onboard.VIEW),
        ]}

    def request(self, service, method, path, token):
        self.calls.append((service, method, path))
        if token != TOKENS[onboard.SECRETS[service]] or method != "GET":
            raise AssertionError("Unexpected auth or write")
        return {("notion", "/users/me"): self.notion_bot,
                ("notion", "/pages/" + PAGE): self.page,
                ("discord", "/users/@me"): self.bot,
                ("discord", "/channels/" + CHAN): self.channel,
                ("discord", "/guilds/" + GUILD): self.guild,
                ("discord", "/guilds/" + GUILD + "/members/" + OWNER): self.owner}[(service, path)]


class Onboarding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "new-state"
        self.api = Fixture()
        self.args = ["--home", str(self.home), "--project", "demo-local",
                     "--notion-page", "https://app.notion.com/p/" + PAGE.replace("-", "") + "?pvs=4",
                     "--discord-channel", "https://discord.com/channels/" + GUILD + "/" + CHAN,
                     "--discord-bot", BOT, "--discord-owner", "expected.user"]

    def call(self, *args, env=TOKENS):
        txt = io.StringIO()
        with contextlib.redirect_stdout(txt):
            code = onboard.cli(self.args + list(args), transport=self.api, env=env)
        for raw in TOKENS.values():
            self.assertNotIn(raw, txt.getvalue())
        return code, json.loads(txt.getvalue())

    def test_no_network_or_disk_in_plan(self):
        code, out = self.call(env={})
        self.assertEqual((code, out["state"], len(out["missing_secret_names"])), (0, "plan", 2))
        self.assertFalse(self.api.calls or self.home.exists())

    def test_missing_secrets_prevents_reads_and_writes(self):
        code, out = self.call("--apply", env={"ANDA_NOTION_TOKEN": TOKENS["ANDA_NOTION_TOKEN"]})
        self.assertEqual((code, out["state"], out["missing_secret_names"]),
                         (2, "blocked_missing_credentials", ["ANDA_DISCORD_TOKEN"]))
        self.assertFalse(self.api.calls or self.home.exists())

    def test_read_preflight_is_not_write_verification(self):
        code, out = self.call("--check")
        self.assertEqual((code, out["state"], out["ids_verified"]["discord_user"]),
                         (0, "read_preflight_verified", OWNER))
        self.assertFalse(out["write_permissions_verified"] or self.home.exists())
        self.assertEqual({method for _, method, _ in self.api.calls}, {"GET"})

    def test_configure_local_only_and_never_store_credential(self):
        code, out = self.call("--apply")
        cfg = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual((code, out["state"], cfg["intake"]["discord"]["owner_ids"]), (0, "configured", [OWNER]))
        self.assertFalse(cfg["provider"]["enabled"])
        self.assertEqual(out["external_writes"], 0)
        self.assertTrue((self.home / "ledger.sqlite3").is_file())
        for secret in TOKENS.values():
            self.assertNotIn(secret, json.dumps(cfg))

    def test_existing_state_not_opened_or_overwritten(self):
        self.home.mkdir()
        orig = self.home / "config.json"
        orig.write_text("existing")
        code, out = self.call("--apply")
        self.assertEqual((code, out["code"], orig.read_text()), (2, "state_path", "existing"))
        self.assertEqual(self.api.calls, [])

    def test_owner_username_mismatch_fails_closed(self):
        self.api.owner["user"]["username"] = "someone.else"
        code, out = self.call("--apply")
        self.assertEqual((code, out["code"]), (2, "discord_owner"))
        self.assertFalse(self.home.exists())

    def test_server_owner_only_fallback_still_requires_expected_human(self):
        self.api.channel["permission_overwrites"].pop()
        code, out = self.call("--check")
        self.assertEqual((code, out["ids_verified"]["discord_user"]), (0, OWNER))
        self.assertIn(("discord", "GET", "/guilds/" + GUILD), self.api.calls)

    def test_wrong_bot_is_rejected_before_channel_access(self):
        self.api.bot["id"] = OTHER
        code, out = self.call("--apply")
        self.assertEqual((code, out["code"]), (2, "discord_identity"))
        self.assertNotIn(("discord", "GET", "/channels/" + CHAN), self.api.calls)

    def test_other_role_and_member_leaks_are_rejected(self):
        for overwrite_rule in [overwrite(OTHER, 0, a=onboard.VIEW), overwrite(OTHER, 1, a=onboard.VIEW)]:
            with self.subTest(overwrite_rule=overwrite_rule):
                self.api.channel["permission_overwrites"].append(overwrite_rule)
                code, out = self.call("--check")
                self.assertEqual((code, out["code"]), (2, "discord_privacy"))
                self.api.channel["permission_overwrites"].pop()

    def test_missing_explicit_bot_permissions_rejected(self):
        self.api.channel["permission_overwrites"][1]["allow"] = str(onboard.VIEW)
        code, out = self.call("--check")
        self.assertEqual((code, out["code"]), (2, "discord_bot_channel_permissions"))

    def test_public_or_malformed_notion_is_rejected(self):
        for public in ("https://notion.example/page", False):
            with self.subTest(public=public):
                self.api.page["public_url"] = public
                code, out = self.call("--apply")
                self.assertEqual((code, out["code"]), (2, "notion_privacy"))
                self.assertFalse(self.home.exists())

    def test_personal_notion_credential_denied(self):
        self.api.notion_bot["type"] = "person"
        code, out = self.call("--apply")
        self.assertEqual((code, out["code"]), (2, "notion_identity"))
        self.assertEqual(self.api.calls, [("notion", "GET", "/users/me")])

    def test_wrong_guild_or_nontext_channel_fails_closed(self):
        self.api.channel["guild_id"] = OTHER
        self.api.channel["type"] = 13
        code, out = self.call("--apply")
        self.assertEqual((code, out["code"]), (2, "discord_scope"))
        self.assertFalse(self.home.exists())

    def test_fake_origin_or_dm_input_rejected_locally(self):
        for url in ("https://discord.com.evil/channels/123/456",
                    "https://discord.com/channels/@me/" + CHAN,
                    "https://test.invalid/channels/" + GUILD + "/" + CHAN):
            with self.subTest(url=url), self.assertRaises(onboard.Blocked):
                onboard.discord_ids(url)


if __name__ == "__main__":
    unittest.main()
