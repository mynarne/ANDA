import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from anda import cli, credentials
from anda.core import AndaError, example_config
from anda.setup import invite_url

APP, GUILD, OWNER = "100000000000000001", "100000000000000002", "100000000000000003"
PAGE = "0" * 31 + "1"
TOKENS = {"notion": "notion-fixture-only-token-123", "discord": "discord.fixture-only.token-456"}
NO_ANDA_ENV = {k: v for k, v in os.environ.items() if not k.startswith("ANDA_")}


class FakeAdvapi:
    """Stands in for advapi32 and reads the real CREDENTIALW struct the module builds."""
    def __init__(self):
        self.items, self.keep = {}, []

    def CredWriteW(self, pointer, flags):
        item = pointer.contents
        self.items[item.TargetName] = (item.Type, item.Persist, item.UserName,
                                       ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize))
        return 1

    def CredReadW(self, target, kind, flags, out):
        if target not in self.items:
            return 0
        blob = self.items[target][3]
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        item = credentials._Credential(TargetName=target, CredentialBlobSize=len(blob),
                                       CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        self.keep += [buffer, item]
        out[0] = ctypes.pointer(item)
        return 1

    def CredFree(self, pointer):
        pass


class Credentials(unittest.TestCase):
    def test_windows_round_trip_is_local_generic_credential(self):
        api = FakeAdvapi()
        with patch.object(credentials, "_advapi", return_value=api):
            self.assertIsNone(credentials.read("ANDA_NOTION_TOKEN", "win32"))
            self.assertEqual(credentials.write("ANDA_NOTION_TOKEN", TOKENS["notion"], "win32"), "windows_credential_manager")
            self.assertEqual(credentials.read("ANDA_NOTION_TOKEN", "win32"), TOKENS["notion"])
        self.assertEqual(api.items["ANDA:ANDA_NOTION_TOKEN"],
                         (credentials.GENERIC, credentials.PERSIST_LOCAL_MACHINE, "ANDA", TOKENS["notion"].encode("utf-16-le")))

    def test_macos_secret_goes_through_stdin_never_argv(self):
        keychain, argvs = {}, []

        def run(argv, input=None, **kw):
            argvs.append(argv)
            if argv == ["security", "-i"]:
                words = input.decode().split()
                keychain[words[words.index("-s") + 1]] = words[words.index("-w") + 1]
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            value = keychain.get(argv[argv.index("-s") + 1])
            return subprocess.CompletedProcess(argv, 0 if value else 44, (value or "").encode() + b"\n", b"")

        with patch.object(credentials.subprocess, "run", side_effect=run):
            self.assertEqual(credentials.write("ANDA_DISCORD_TOKEN", TOKENS["discord"], "darwin"), "macos_keychain")
            self.assertEqual(credentials.read("ANDA_DISCORD_TOKEN", "darwin"), TOKENS["discord"])
        self.assertFalse(any(TOKENS["discord"] in arg for argv in argvs for arg in argv))

    def test_rejects_odd_values_and_has_no_store_elsewhere(self):
        with self.assertRaises(AndaError):
            credentials.write("ANDA_NOTION_TOKEN", "short", "win32")
        with self.assertRaises(AndaError):
            credentials.read("OTHER_TOKEN", "win32")
        self.assertIsNone(credentials.write("ANDA_NOTION_TOKEN", TOKENS["notion"], "linux"))
        self.assertIsNone(credentials.read("ANDA_NOTION_TOKEN", "linux"))

    def test_runtime_fills_only_missing_enabled_connectors(self):
        cfg = example_config()
        cfg["destinations"]["notion"]["enabled"] = True
        with patch.dict(os.environ, NO_ANDA_ENV, clear=True), patch.object(credentials, "read", return_value="stored-value-0123456789"):
            self.assertEqual(credentials.load_env(cfg), ["ANDA_NOTION_TOKEN"])
            self.assertNotIn("ANDA_DISCORD_TOKEN", os.environ)
        with patch.dict(os.environ, {"ANDA_NOTION_TOKEN": "explicit-env-value-123"}), patch.object(credentials, "read") as read:
            self.assertEqual(credentials.load_env(cfg), [])
            read.assert_not_called()


class Wizard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "state"
        self.project = Path(self.tmp.name) / "project"
        (self.project / ".claude").mkdir(parents=True)
        (self.project / ".claude" / "settings.local.json").write_text('{"permissions": {"allow": ["Bash(ls)"]}}', encoding="utf-8")
        self.store, self.calls, self.missing_member = {}, [], None

    def request(self, service, method, path, token, body=None):
        self.assertEqual(token, TOKENS[service])
        self.calls.append((service, method, path))
        if method == "POST":
            n = sum(1 for call in self.calls if call[1] == "POST")
            return {"id": format(n, "032x") if service == "notion" else str(90000 + n)}
        ident = path.rsplit("/", 1)[-1]
        if path == "/users/me":
            return {"type": "bot", "id": "b" * 32}
        if service == "notion":
            return {"id": ident, "object": "page", "public_url": None}
        if path == "/users/@me":
            return {"id": APP, "bot": True}
        if "/members/" in path:
            if ident == self.missing_member:
                from anda.connectors import DeliveryError
                raise DeliveryError("failed", "API rejected request: HTTP 404")
            return {"user": {"id": ident, "bot": ident == APP}}
        if path.endswith("/roles"):
            return [{"id": GUILD}, {"id": "22222"}]
        return {"id": ident}

    def setup(self, *answers, flags=()):
        transport = type("T", (), {"request": lambda _, *a, **k: self.request(*a, **k)})()
        fake_write = lambda name, value: self.store.__setitem__(name, value) or "fake_store"
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, NO_ANDA_ENV, clear=True), \
             patch("anda.connectors.Transport", return_value=transport), \
             patch.object(credentials, "read", side_effect=self.store.get), \
             patch.object(credentials, "write", side_effect=fake_write), \
             patch("anda.setup._hidden", side_effect=[TOKENS["notion"], TOKENS["discord"]]), \
             patch("builtins.input", side_effect=list(answers)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--home", str(self.home), "setup", "--project", "demo", "--discord-app", APP,
                             "--discord-guild", GUILD, "--owner-id", OWNER,
                             "--notion-parent", "https://www.notion.so/ANDA-Test-" + PAGE + "?pvs=4", *flags])
        text = out.getvalue() + err.getvalue()
        for token in TOKENS.values():
            self.assertNotIn(token, text)
            for path in self.home.rglob("*"):
                if path.is_file():
                    self.assertNotIn(token.encode(), path.read_bytes())
        return code, out.getvalue(), err.getvalue()

    def test_invite_url_has_minimum_bot_permissions(self):
        self.assertEqual(invite_url(APP), "https://discord.com/oauth2/authorize?client_id=" + APP + "&scope=bot&permissions=68608")
        self.assertEqual(invite_url(APP, GUILD, create_channel=True),
                         "https://discord.com/oauth2/authorize?client_id=" + APP + "&scope=bot&permissions=68624"
                         "&guild_id=" + GUILD + "&disable_guild_select=true")
        with self.assertRaises(AndaError):
            invite_url("not-an-id")

    def test_full_setup_needs_explicit_yes_and_never_prints_secrets(self):
        code, out, _ = self.setup("", "yes", "y", flags=("--claude-project", str(self.project)))
        result = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((code, result["state"], result["provisioned"]), (0, "configured", True))
        self.assertIn(invite_url(APP, GUILD, create_channel=True), out)
        self.assertEqual(self.store, {"ANDA_NOTION_TOKEN": TOKENS["notion"], "ANDA_DISCORD_TOKEN": TOKENS["discord"]})
        self.assertEqual(set(result["credential_sources"].values()), {"fake_store"})
        self.assertEqual({item["state"] for item in result["doctor"].values()}, {"read_access_confirmed"})
        first_post = next(i for i, call in enumerate(self.calls) if call[1] == "POST")
        self.assertTrue(all(call[1] == "GET" for call in self.calls[:first_post]))
        config = json.loads((self.home / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["destinations"]["notion"]["token_env"], "ANDA_NOTION_TOKEN")
        settings = json.loads((self.project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"], {"allow": ["Bash(ls)"]})
        hook = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual((hook["command"], hook["args"][-6:]), (sys.executable, ["hook", "--project", "demo", "--provider", "claude", "--native"]))
        self.assertEqual(set(settings["hooks"]), {"SessionStart", "UserPromptSubmit", "Stop"})
        server = json.loads((self.project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["anda"]
        self.assertEqual(server["args"][-5:], ["mcp", "--agent", "claude", "--project", "demo"])

    def test_declining_creates_and_writes_nothing(self):
        code, out, _ = self.setup("", "n", str(self.project), "n")
        result = json.loads(out.strip().splitlines()[-1])
        self.assertEqual((code, result["state"], result["claude_files_written"]), (0, "checked_not_applied", []))
        self.assertFalse([call for call in self.calls if call[1] != "GET"])
        self.assertFalse((self.project / ".mcp.json").exists())
        self.assertFalse(json.loads((self.home / "config.json").read_text(encoding="utf-8"))["destinations"]["discord"]["enabled"])

    def test_project_folder_behind_a_link_or_inside_state_is_refused(self):
        from anda.setup import write_claude_project
        link = Path(self.tmp.name) / "linked"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(self.project), str(link))  # junctions need no admin rights
        else:
            link.symlink_to(self.project, target_is_directory=True)
        self.addCleanup(os.rmdir if os.name == "nt" else os.unlink, link)
        with self.assertRaises(AndaError):
            write_claude_project(str(link), {"Stop": []}, {}, self.home)
        # Containment compares canonical paths, so a different spelling of the state folder is still inside it.
        with self.assertRaises(AndaError):
            write_claude_project(str(self.project), {"Stop": []}, {}, str(self.project / ".." / "project"))
        self.assertFalse((self.project / ".mcp.json").exists())

    def test_failed_read_check_stores_nothing(self):
        self.missing_member = APP  # the owner never clicked Authorize
        code, _, err = self.setup("")
        self.assertEqual(code, 2)
        self.assertIn("Bot membership", err)
        self.assertEqual(self.store, {})
        self.assertFalse([call for call in self.calls if call[1] != "GET"])


if __name__ == "__main__":
    unittest.main()
