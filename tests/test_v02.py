import contextlib
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from anda.core import AndaError, Ledger, example_config
from anda.engine import work_job
from anda.setup import provision, client_config
from anda.backup import snapshot
from anda.connectors import DeliveryError

class FakeSetupAPI:
    """Synthetic receipts only. No external service is called."""
    def __init__(self, fail=None):
        self.posts = []
        self.fail = fail
    def call(self, service, method, path, body=None):
        if method == "GET":
            if path == "/users/me": return {"id": "b"*32, "type": "bot"}
            if path == "/users/@me": return {"id": "77777", "bot": True}
            if "/members/" in path: return {"user": {"id": path.rsplit("/", 1)[-1]}}
            if path.endswith("/roles"): return [{"id":"11111"},{"id":"22222"}]
            return {"id":"a"*32}
        self.posts.append((service, path, body))
        if self.fail:
            raise DeliveryError(self.fail, "Synthetic provisioning error")
        return {"id": (format(len(self.posts), "032x") if service == "notion" else str(90000 + len(self.posts)))}

class V02Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)/"state"
        self.cfg = example_config("demo")
        self.now = 100000
        self.ledger = Ledger(self.home, self.cfg, clock=lambda: self.now)
        (self.home/"config.json").write_text(json.dumps(self.cfg))
        if os.name != "nt": (self.home/"config.json").chmod(0o600)
    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()
    def request(self, ident):
        return self.ledger.record("demo","operator","requests",ident,"Answer with evidence",kind="request",job=True)
    def test_provision_plan_is_zero_network_zero_mutation(self):
        api = FakeSetupAPI()
        before = self.ledger.config
        result = provision(self.ledger,"demo",notion_parent="a"*32,guild="11111",owner="33333",connectors=api)
        self.assertFalse(result["network_calls"])
        self.assertEqual(api.posts, [])
        self.assertEqual(self.ledger.config,before)
        self.assertIsNone(self.ledger.db.execute("SELECT name FROM sqlite_master WHERE name='provisioning'").fetchone())
    def test_provision_new_scoped_resources_and_repeat(self):
        api = FakeSetupAPI()
        kw = dict(notion_parent="a"*32,guild="11111",owner="33333",notion_user="c"*32,apply=True,connectors=api)
        first = provision(self.ledger,"demo",**kw)
        second = provision(self.ledger,"demo",**kw)
        self.assertEqual(first["receipts"],second["receipts"])
        self.assertEqual(len(api.posts),7)
        self.assertTrue(self.ledger.config["intake"]["notion"]["enabled"])
        self.assertFalse(self.ledger.config["provider"]["enabled"])
        self.assertEqual(self.ledger.config["destinations"]["notion"]["parent_page_id"],first["receipts"]["notion_releases"])
        for service,path,body in api.posts:
            if service=="discord":
                denied = {x["id"] for x in body["permission_overwrites"] if x["type"]==0 and x["deny"]=="1024"}
                self.assertEqual(denied,{"11111","22222"})
                allowed = {x["id"] for x in body["permission_overwrites"] if x["type"]==1}
                self.assertEqual(allowed,{"33333","77777"})
            self.assertIn(path,["/pages","/guilds/11111/channels"])
    def test_provision_unknown_never_posts_again(self):
        api = FakeSetupAPI("unknown")
        for _ in range(2):
            with self.assertRaises(AndaError):
                provision(self.ledger,"demo",notion_parent="a"*32,apply=True,connectors=api)
        self.assertEqual(len(api.posts),1)
        self.assertFalse(self.ledger.config["destinations"]["notion"]["enabled"])
    def test_provision_explicit_rejection_can_be_retried(self):
        api = FakeSetupAPI("blocked")
        with self.assertRaises(AndaError):
            provision(self.ledger,"demo",notion_parent="a"*32,apply=True,connectors=api)
        api.fail = None
        self.assertEqual(provision(self.ledger,"demo",notion_parent="a"*32,apply=True,connectors=api)["state"],"configured")
        self.assertEqual(len(api.posts),6)
    def test_provision_wont_replace_existing_live_targets(self):
        self.cfg["destinations"]["discord"].update(enabled=True,channel_id="44444")
        self.ledger.config=copy.deepcopy(self.cfg)
        api=FakeSetupAPI()
        with self.assertRaises(AndaError):
            provision(self.ledger,"demo",guild="11111",owner="33333",apply=True,connectors=api)
        self.assertEqual(api.posts,[])
    def test_client_configs_are_project_bound_no_secrets(self):
        for client in ["claude","gemini"]:
            result=json.loads(client_config(self.ledger,"demo",client))
            args=result["mcpServers"]["anda"]["args"]
            self.assertEqual(args[-2:],["--project","demo"])
            self.assertEqual(args[0],str(Path(__file__).resolve().parents[1]/"scripts"/"anda.py"))
            self.assertNotIn("env",result["mcpServers"]["anda"])
        import tomllib
        for client in ["codex","grok"]:
            parsed=tomllib.loads(client_config(self.ledger,"demo",client))
            self.assertEqual(parsed["mcp_servers"]["anda"]["args"][-1],"demo")
        with self.assertRaises(AndaError): client_config(self.ledger,"other","claude")
    def test_full_snapshot_is_consistent_private_and_no_overwrite(self):
        for i in range(120):
            self.ledger.record("demo","operator","notes",str(i),"Full archival text "+str(i))
        target=Path(self.tmp.name)/"private-backup"
        result=snapshot(self.ledger,target)
        self.assertTrue(result["private"])
        with contextlib.closing(sqlite3.connect(target/"ledger.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0],120)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        if os.name != "nt":
            self.assertEqual(target.stat().st_mode & 0o777,0o700)
            self.assertEqual((target/"ledger.sqlite3").stat().st_mode & 0o777,0o600)
        with self.assertRaises(AndaError): snapshot(self.ledger,target)
        with self.assertRaises(AndaError): snapshot(self.ledger,self.home/"backup")
    def test_daily_budget_keeps_later_jobs_pending_then_resets(self):
        self.ledger.config["provider"].update(enabled=True,max_calls_per_day=1)
        self.request("one");self.request("two")
        calls=[]
        fn=lambda *args: calls.append(args) or "Output"
        work_job(self.ledger,fn);work_job(self.ledger,fn)
        self.assertEqual(len(calls),1)
        self.assertEqual([x["state"] for x in self.ledger.status()["jobs"]],["completed","pending"])
        self.assertEqual(self.ledger.status()["provider_budget"]["attempts_reserved"],1)
        self.now+=86400
        work_job(self.ledger,fn)
        self.assertEqual(len(calls),2)
    def test_provider_change_never_reroutes_old_job(self):
        self.ledger.config["provider"]["enabled"]=True
        self.request("frozen")
        self.ledger.config["provider"]["name"]="claude"
        calls=[]
        work_job(self.ledger,lambda *args:calls.append(args) or "Wrong provider")
        self.assertEqual(calls,[])
        self.assertEqual(self.ledger.status()["jobs"][0]["state"],"blocked")
    def test_claude_adapter_uses_stdin_tools_off_and_no_connector_secrets(self):
        from anda.providers import claude_answer
        seen={}
        def run(command,**kw):
            seen.update(command=command,**kw)
            kw["stdout"].write(b"Safe answer")
            return subprocess.CompletedProcess(command,0)
        with patch.dict(os.environ,{"ANDA_DISCORD_TOKEN":"synthetic-private-token"}), patch("anda.providers.shutil.which",return_value="/fake/claude"), patch("anda.providers.subprocess.run",side_effect=run):
            answer=claude_answer("Visible request",str(self.home))
        self.assertEqual(answer,"Safe answer")
        self.assertNotIn("Visible request",seen["command"])
        self.assertEqual(seen["input"],b"Visible request")
        self.assertNotIn("ANDA_DISCORD_TOKEN",seen["env"])
        self.assertEqual(seen["command"][seen["command"].index("--tools")+1],"")
        self.assertIn("--no-session-persistence",seen["command"])
        self.assertIn("--safe-mode",seen["command"])
        self.assertIn("--strict-mcp-config",seen["command"])
    def test_uninstalled_claude_fails_without_network(self):
        from anda.providers import claude_answer
        with patch("anda.providers.shutil.which",return_value=None):
            with self.assertRaises(AndaError):claude_answer("test",str(self.home))

    def test_remote_stdio_is_one_host_and_shell_quoted(self):
        import shlex
        result=json.loads(client_config(self.ledger,"demo","claude","anda-host","/opt/anda package/scripts/anda.py","/srv/anda private"))
        entry=result["mcpServers"]["anda"]
        self.assertEqual(entry["command"],"ssh")
        self.assertIn("StrictHostKeyChecking=yes",entry["args"])
        command=shlex.split(entry["args"][-1])
        self.assertEqual(command,["python3","/opt/anda package/scripts/anda.py","--home","/srv/anda private","mcp","--agent","claude","--project","demo"])
        with self.assertRaises(AndaError):client_config(self.ledger,"demo","claude","-oProxyCommand=evil","/x","/y")
        with self.assertRaises(AndaError):client_config(self.ledger,"demo","claude","host","relative","/y")

    def test_public_notion_parent_rejected_before_creation(self):
        class PublicParent(FakeSetupAPI):
            def call(self,service,method,path,body=None):
                if method=="GET" and path.startswith("/pages/"):
                    return {"public_url":"https://example.invalid/public-page"}
                return super().call(service,method,path,body)
        api=PublicParent()
        with self.assertRaises(AndaError):
            provision(self.ledger,"demo",notion_parent="a"*32,apply=True,connectors=api)
        self.assertEqual(api.posts,[])
    def test_daily_budget_reservation_is_atomic_across_connections(self):
        import concurrent.futures
        self.ledger.config["provider"].update(enabled=True,max_calls_per_day=1)
        self.request("race-a");self.request("race-b")
        def claim(_):
            conn=Ledger(self.home,copy.deepcopy(self.ledger.config),clock=lambda:self.now)
            try:return bool(conn.claim("jobs"))
            finally:conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(claim,range(2)))
        self.assertEqual(sum(results),1)
        self.assertEqual(self.ledger.status()["provider_budget"]["attempts_reserved"],1)
    def test_codex_adapter_scopes_state_and_disables_extra_tools(self):
        from anda.engine import codex_answer
        seen={}
        def run(command,**kw):
            seen.update(command=command,**kw)
            Path(command[command.index("--output-last-message")+1]).write_text("Safe answer")
            return subprocess.CompletedProcess(command,0)
        original_home=os.environ.get("HOME")
        with patch.dict(os.environ,{"ANDA_DISCORD_TOKEN":"synthetic-private-token"}),patch("anda.engine.shutil.which",return_value="/fake/codex"),patch("anda.engine.subprocess.run",side_effect=run):
            self.assertEqual(codex_answer("Visible request",str(self.home)),"Safe answer")
        self.assertNotIn("Visible request",seen["command"])
        self.assertNotIn("ANDA_DISCORD_TOKEN",seen["env"])
        self.assertEqual(seen["env"].get("HOME"),original_home)
        self.assertIn("shell_tool",seen["command"])
        self.assertIn('web_search="disabled"',seen["command"])
        state_config = next(x for x in seen["command"] if x.startswith("sqlite_home="))
        self.assertTrue(Path(json.loads(state_config.split("=", 1)[1])).is_relative_to(self.home))
