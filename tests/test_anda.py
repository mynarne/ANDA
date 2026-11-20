import concurrent.futures
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"runtime"))
from anda.core import AndaError, Ledger, example_config, load_config, redact
from anda.connectors import Connectors, DeliveryError
from anda.engine import deliver_one, reconcile, work_job, pull_discord, pull_notion
from anda.cli import capture_hook

ROOT=Path(__file__).resolve().parents[1]

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)/"state"
        self.now=1000
        self.cfg=example_config("alpha")
        self.cfg["projects"].append("beta")
        self.l=Ledger(self.home,self.cfg,clock=lambda:self.now)
    def tearDown(self):
        self.l.close()
        self.tmp.cleanup()
    def connected(self):
        self.cfg["destinations"]["notion"].update(enabled=True,parent_page_id="a"*32)
        self.cfg["destinations"]["discord"].update(enabled=True,channel_id="123456789")
        self.l.config=copy.deepcopy(self.cfg)
    def record(self,**kw):
        x=dict(project="alpha",source="operator",conversation="c1",source_id="event1",text="keep evidence")
        x.update(kw)
        return self.l.record(**x)
    def test_idempotency_and_conflict(self):
        self.connected()
        a=self.record()
        for _ in range(10):self.assertTrue(self.record()["duplicate"])
        self.assertEqual(self.l.status()["events"],1)
        self.assertEqual(len(self.l.status()["deliveries"]),2)
        with self.assertRaises(AndaError):self.record(text="edited with same identity")
        self.assertEqual(self.l.status()["events"],1)
    def test_scope_separation(self):
        self.record(text="alpha-only")
        self.record(project="beta",text="beta-only")
        self.assertNotIn("beta-only",json.dumps(self.l.context("alpha")))
        with self.assertRaises(AndaError):self.l.context("outside")
    def test_decisions_need_operator(self):
        with self.assertRaises(AndaError):self.record(source="mcp",kind="decision")
        self.record(kind="decision")
        self.assertEqual(len(self.l.context("alpha")["decisions"]),1)
    def test_no_secrets_stored_or_forwarded(self):
        with patch.dict(os.environ,{"ANDA_NOTION_TOKEN":"private-connector-secret-12345"}):
            self.record(text="private-connector-secret-12345 api_key=sk-secretvaluefortest12345")
        text=self.l.context("alpha")["events"][0]["text"]
        self.assertNotIn("private-connector",text)
        self.assertNotIn("sk-secret",text)
        self.assertIn("REDACTED",text)
    def test_two_destination_independence(self):
        self.connected();self.record()
        class C:
            def send(_,e,d):
                if d["destination"]=="notion":raise DeliveryError("pending","rate limit",120)
                return "discord-123"
        deliver_one(self.l,C());deliver_one(self.l,C())
        states={x["destination"]:x["state"] for x in self.l.status()["deliveries"]}
        self.assertEqual(states,{"notion":"pending","discord":"confirmed"})
        self.assertIsNone(deliver_one(self.l,C()))
    def test_post_unknown_is_not_replayed(self):
        self.connected();self.record()
        calls=[]
        class C:
            def send(_,e,d):
                calls.append(d["id"]);raise DeliveryError("unknown","response lost")
        for _ in range(5):deliver_one(self.l,C())
        self.assertEqual(len(calls),2)
        self.assertTrue(all(x["state"]=="unknown" for x in self.l.status()["deliveries"]))
    def test_reconcile_positive_only(self):
        self.connected();self.record()
        d=self.l.claim("outbox");self.l.finish("outbox",d["id"],d["fence"],"unknown")
        class C:
            def reconcile(_,e,d):return None
        reconcile(self.l,C())
        self.assertEqual(self.l.status()["deliveries"][0]["state"],"unknown")
        self.now+=301
        class Found:
            def reconcile(_,e,d):return "matched-123"
        reconcile(self.l,Found())
        self.assertEqual(self.l.status()["deliveries"][0]["receipt"],"matched-123")
    def test_expired_lease_unknown_and_stale_fence(self):
        self.connected();self.record()
        d=self.l.claim("outbox");self.now+=60;self.l.recover()
        with self.assertRaises(AndaError):self.l.finish("outbox",d["id"],d["fence"],"confirmed",receipt="late123")
        self.assertEqual(self.l.status()["deliveries"][0]["state"],"unknown")
    def test_bounded_retry_exhaustion(self):
        self.connected();self.l.config["retry"]["max_attempts"]=2;self.record()
        class C:
            def send(_,e,d):raise DeliveryError("pending","rate limited",1)
        for _ in range(4):
            deliver_one(self.l,C());self.now+=3600
        self.assertTrue(all(x["state"]=="failed" for x in self.l.status()["deliveries"]))
    def test_explicit_resolution(self):
        self.connected();self.record()
        d=self.l.claim("outbox");self.l.finish("outbox",d["id"],d["fence"],"unknown")
        with self.assertRaises(AndaError):self.l.resolve(d["id"],not_sent=True)
        self.l.resolve(d["id"],not_sent=True,evidence="Operator inspected configured destination")
        self.assertEqual(self.l.status()["deliveries"][0]["state"],"pending")
        self.assertEqual(self.l.db.execute("SELECT count(*) FROM audit WHERE action='operator_resolve'").fetchone()[0],1)
    def test_provider_output_not_semantic_verification(self):
        self.connected();self.l.config["provider"]["enabled"]=True
        self.record(kind="request",job=True)
        work_job(self.l,provider=lambda prompt,home,timeout:"This is a simulated test response.")
        self.assertEqual(self.l.status()["jobs"][0]["state"],"completed")
        self.assertEqual(self.l.status()["events"],2)
        self.assertEqual(len(self.l.status()["deliveries"]),4)
        # No repeated model execution after outputs are in the outbox.
        work_job(self.l,provider=lambda *_:self.fail("rerun"))
    def test_provider_crash_preserves_unknown(self):
        self.l.config["provider"]["enabled"]=True
        self.record(kind="request",job=True)
        def bad(*args):raise RuntimeError("secret must not leak")
        work_job(self.l,provider=bad)
        self.assertEqual(self.l.status()["jobs"][0]["state"],"unknown")
        self.assertNotIn("secret must",json.dumps(self.l.status()))
        self.assertIsNone(work_job(self.l,provider=bad))
    def test_crash_after_claim_does_not_repeat_paid_job(self):
        self.record(kind="request",job=True)
        j=self.l.claim("jobs");self.now+=200;self.l.recover()
        self.assertEqual(self.l.status()["jobs"][0]["state"],"unknown")
        self.assertIsNone(self.l.claim("jobs"))
    def test_hook_visible_payload_and_no_transcript_read(self):
        for name in ["codex","claude","gemini","grok"]:
            p={"session_id":"s-"+name,"hook_event_name":"UserPromptSubmit","prompt":"visible",
               "transcript_path":"/this/must/not/be/read"}
            capture_hook(self.l,"alpha",name,p,event_id="t1")
            self.assertTrue(capture_hook(self.l,"alpha",name,p,event_id="t1")["duplicate"])
        self.assertEqual(self.l.status()["events"],4)
        self.assertEqual(capture_hook(self.l,"alpha","grok",{"sessionId":"g","hookEventName":"Stop"})["state"],"unsupported_or_empty")
    def test_hook_generated_identity_is_disclosed(self):
        r=capture_hook(self.l,"alpha","claude",{"session_id":"c","hook_event_name":"UserPromptSubmit","prompt":"hello"})
        self.assertTrue(r["identity_generated"])
    def test_discord_baseline_allowlist_and_echo(self):
        self.connected()
        self.l.config["intake"]["discord"].update(enabled=True,project="alpha",owner_ids=["owner"])
        messages=[{"id":"100","author":{"id":"owner"},"content":"!anda old"}]
        class C:
            def call(_, *args):return messages
        self.assertEqual(pull_discord(self.l,C())["state"],"baseline")
        messages[:]=[{"id":"103","author":{"id":"bot","bot":True},"content":"!anda echo"},
                     {"id":"102","author":{"id":"stranger"},"content":"!anda nope"},
                     {"id":"101","author":{"id":"owner"},"content":"!anda new"}]
        self.assertEqual(pull_discord(self.l,C())["count"],1)
        self.assertEqual(self.l.status()["events"],1)
    def test_notion_baseline_and_command_revision(self):
        self.connected()
        self.l.config["intake"]["notion"].update(enabled=True,page_id="b"*32,project="alpha",user_ids=["human"])
        def block(text):return {"id":"block1","type":"paragraph","last_edited_by":{"id":"human"},
                            "paragraph":{"rich_text":[{"plain_text":text}]}}
        blocks=[block("!anda old")]
        class C:
            def pages(_, *args):return blocks
        self.assertEqual(pull_notion(self.l,C())["state"],"baseline")
        blocks[:]=[block("!anda current")]
        self.assertEqual(pull_notion(self.l,C())["count"],1)
        self.assertEqual(pull_notion(self.l,C())["count"],0)
    def test_rest_payload_scope_and_mentions(self):
        self.connected();event=self.record()
        e=dict(self.l.db.execute("SELECT * FROM events WHERE id=?",(event["id"],)).fetchone())
        calls=[]
        class T:
            def request(_,name,method,path,token,body=None):
                calls.append((name,method,path,token,body));return {"id":"receipt-123"}
        c=Connectors(self.l.config,T())
        with patch.dict(os.environ,{"ANDA_NOTION_TOKEN":"notion-secret","ANDA_DISCORD_TOKEN":"discord-secret"}):
            for d in self.l.status()["deliveries"]:c.send(e,d)
        self.assertEqual(calls[0][4]["parent"],{"page_id":"a"*32})
        self.assertEqual(calls[1][2],"/channels/123456789/messages")
        self.assertEqual(calls[1][4]["allowed_mentions"],{"parse":[]})
        self.assertTrue(calls[1][4]["enforce_nonce"])
    def test_missing_secrets_block_without_network(self):
        self.connected();event=self.record()
        class T:
            def request(*args):raise AssertionError("network")
        with patch.dict(os.environ,{},clear=True):
            deliver_one(self.l,Connectors(self.l.config,T()))
        self.assertEqual(self.l.status()["deliveries"][0]["state"],"blocked")
    def test_config_literal_secret_rejected(self):
        from anda.core import validate_config
        c=example_config();c["destinations"]["discord"]["token"]="do-not-store"
        with self.assertRaises(AndaError):validate_config(c)
    def test_concurrent_ingress_has_one_event(self):
        self.connected()
        def insert(_):
            l=Ledger(self.home,self.l.config)
            try:return l.record("alpha","operator","same","same","same")
            finally:l.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(insert,range(20)))
        self.assertEqual(self.l.status()["events"],1)
        self.assertEqual(len(self.l.status()["deliveries"]),2)
    def test_destination_change_never_reroutes_backlog(self):
        self.connected();self.record()
        self.l.config["destinations"]["notion"]["parent_page_id"]="b"*32
        class T:
            def request(*args):raise AssertionError("must not send to new destination")
        deliver_one(self.l,Connectors(self.l.config,T()))
        self.assertEqual(self.l.status()["deliveries"][0]["state"],"blocked")
    def test_context_budget_is_bounded_and_excerpts_marked(self):
        for i in range(20):self.record(source_id=str(i),text="한"*10000)
        result=self.l.context("alpha",limit=100)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(sum(len(e["text"].encode()) for e in result["events"]),16000)
    def test_unrelated_directory_not_modified(self):
        from anda.core import private_dir
        other=Path(self.tmp.name)/"unrelated";other.mkdir();(other/"valuable.txt").write_text("keep")
        original=other.stat().st_mode
        with self.assertRaises(AndaError):private_dir(other)
        self.assertEqual(other.stat().st_mode,original)
    def test_native_hook_context_envelopes(self):
        from anda.cli import native_hook_result
        self.record(kind="decision")
        for provider in ["codex","claude","gemini"]:
            r=native_hook_result(provider,{"hook_event_name":"SessionStart"},{"persisted":False},self.l,"alpha")
            self.assertIn("keep evidence",r["hookSpecificOutput"]["additionalContext"])
            if provider in {"codex","claude"}:self.assertEqual(r["hookSpecificOutput"]["hookEventName"],"SessionStart")
        with patch("sys.stderr",io.StringIO()):
            self.assertEqual(native_hook_result("grok",{"hookEventName":"SessionStart"},{},self.l,"alpha"),{})
    def test_reconciliation_rejects_spoofed_notion_page_author(self):
        self.connected();r=self.record()
        event=dict(self.l.db.execute("SELECT * FROM events WHERE id=?",(r["id"],)).fetchone())
        d=self.l.status()["deliveries"][0]
        title=Connectors(self.l.config).notion_title(event,d)
        class T:
            def request(_,name,method,path,token,body=None):
                if path=="/users/me":return {"id":"connection-bot","type":"bot"}
                return {"results":[{"id":"fake-page","type":"child_page","created_by":{"id":"other-user"},"child_page":{"title":title}}],"has_more":False}
        with patch.dict(os.environ,{"ANDA_NOTION_TOKEN":"synthetic-token"}):
            self.assertIsNone(Connectors(self.l.config,T()).reconcile(event,d))
    def test_transport_429_and_committed_503_classification(self):
        import urllib.error
        from anda.connectors import Transport
        class Opener:
            def __init__(self,status,body):self.status,self.body=status,body
            def open(self,req,timeout):
                raise urllib.error.HTTPError(req.full_url,self.status,"simulated",{"Retry-After":"47"},io.BytesIO(json.dumps(self.body).encode()))
        for status,body,state in [(429,{},"pending"),(503,{"additional_data":{"committed_resource_id":"page-123"}},"unknown")]:
            with patch("urllib.request.build_opener",return_value=Opener(status,body)):
                with self.assertRaises(DeliveryError) as ctx:Transport().request("notion","POST","/pages","synthetic",{})
                self.assertEqual(ctx.exception.state,state)
                if status==429:self.assertEqual(ctx.exception.delay,47)
                else:self.assertEqual(ctx.exception.receipt,"page-123")
    def test_transaction_rolls_back_partial_enqueue(self):
        self.connected()
        with patch.object(self.l,"enqueue",side_effect=RuntimeError("simulated disk failure")):
            with self.assertRaises(RuntimeError):self.record()
        self.assertEqual(self.l.status()["events"],0)
        self.assertEqual(self.l.status()["deliveries"],[])
    def test_provider_filesystem_failure_has_safe_diagnostic(self):
        from anda.engine import codex_answer
        from types import SimpleNamespace
        def simulate(*args,**kwargs):
            kwargs["stderr"].write(b"Error: failed to initialize app-server: Read-only file system (os error 30)")
            return SimpleNamespace(returncode=1)
        with patch("shutil.which",return_value="/synthetic/codex"),patch("subprocess.run",side_effect=simulate):
            with self.assertRaisesRegex(AndaError,"read-only filesystem"):
                codex_answer("synthetic prompt",str(self.home),10)
    def test_cli_and_mcp_real_process(self):
        state=Path(self.tmp.name)/"cli-state"
        base=[sys.executable,str(ROOT/"scripts/anda.py"),"--home",str(state)]
        def run(args,input=""):
            return subprocess.run(base+args,input=input,text=True, encoding="utf-8",capture_output=True,check=False)
        self.assertEqual(run(["init","--project","alpha"]).returncode,0)
        mcp=run(["mcp","--project","alpha"],"\n".join([
            json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}),
            json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_record","arguments":{"conversation":"c","event_id":"1","text":"real local IPC test"}}}),
            json.dumps({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"memory_context","arguments":{}}}),
            json.dumps({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"memory_context","arguments":{"project":"beta"}}}),
        ])+"\n")
        rows=[json.loads(x) for x in mcp.stdout.splitlines()]
        self.assertEqual(rows[1]["result"]["isError"],False)
        self.assertIn("real local IPC test",rows[2]["result"]["content"][0]["text"])
        self.assertIn("error",rows[3])
        malformed=run(["mcp","--project","alpha"],'[]\n{"jsonrpc":"2.0","id":5,"method":"ping"}\n')
        malformed_rows=[json.loads(x) for x in malformed.stdout.splitlines()]
        self.assertIn("error",malformed_rows[0]);self.assertEqual(malformed_rows[1]["result"],{})
        self.assertEqual(run(["worker"]).returncode,0)
        self.assertEqual(run(["init","--project","alpha"]).returncode,2)
    def test_install_is_copy_only_and_repeat_safe(self):
        target=Path(self.tmp.name)/"installed"
        cmd=[sys.executable,str(ROOT/"scripts/install.py"),"--target",str(target)]
        r=subprocess.run(cmd,capture_output=True)
        self.assertEqual(r.returncode,0);self.assertFalse(target.exists())
        self.assertEqual(subprocess.run(cmd+["--apply"],capture_output=True).returncode,0)
        self.assertTrue((target/"SKILL.md").is_file())
        self.assertNotEqual(subprocess.run(cmd+["--apply"],capture_output=True).returncode,0)
        self.assertFalse((target/"config.json").exists())
    def test_cli_stdio_is_utf8_and_hook_survives_large_payload(self):
        # Windows pipes default to the ANSI code page (cp949/cp1252); hook and MCP peers read UTF-8.
        env={k:v for k,v in os.environ.items() if k not in {"PYTHONUTF8","PYTHONIOENCODING"}}
        base=[sys.executable,str(ROOT/"scripts/anda.py"),"--home",str(Path(self.tmp.name)/"utf8-state")]
        def run(args,lines):
            data="".join(json.dumps(x,ensure_ascii=False)+"\n" for x in lines).encode("utf-8")
            return subprocess.run(base+args,input=data,capture_output=True,env=env,timeout=30)
        self.assertEqual(run(["init","--project","alpha"],[]).returncode,0)
        hook={"session_id":"s","hook_event_name":"UserPromptSubmit","prompt_id":"p1","prompt":"한글 메모 🚀"}
        self.assertEqual(run(["hook","--project","alpha","--provider","claude"],[hook]).returncode,0)
        # A pasted prompt beyond the 32000-byte text limit is clipped, and context is still injected.
        native=run(["hook","--project","alpha","--provider","claude","--native"],[{**hook,"prompt_id":"p2","prompt":"가"*20000}])
        self.assertIn("🚀",json.loads(native.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"])
        mcp=run(["mcp","--project","alpha"],[
            {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory_context","arguments":{}}},
            {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_put","arguments":{
                "kind":"progress","key":"k","text":"t","source_event_id":"missing","operation_id":"o1"}}}])
        rows=[json.loads(x) for x in mcp.stdout.decode("utf-8").splitlines()]
        self.assertIn("🚀",rows[0]["result"]["content"][0]["text"])
        # A failed tool call tells the agent why instead of a generic protocol error.
        self.assertTrue(rows[1]["result"]["isError"])
        self.assertIn("source event",rows[1]["result"]["content"][0]["text"])
    def test_claude_prompt_id_is_stable_hook_identity(self):
        p={"session_id":"c","hook_event_name":"UserPromptSubmit","prompt_id":"0b6c1d2e-prompt","prompt":"hello"}
        self.assertFalse(capture_hook(self.l,"alpha","claude",p)["identity_generated"])
        self.assertTrue(capture_hook(self.l,"alpha","claude",p)["duplicate"])
        stop={**p,"hook_event_name":"Stop"}
        self.assertTrue(capture_hook(self.l,"alpha","claude",{**stop,"last_assistant_message":"first answer"})["persisted"])
        # A later Stop for the same prompt (e.g. after background work) is a new record, not an identity conflict.
        self.assertTrue(capture_hook(self.l,"alpha","claude",{**stop,"last_assistant_message":"after background work"})["persisted"])
        self.assertTrue(capture_hook(self.l,"alpha","claude",{**stop,"last_assistant_message":"first answer"})["duplicate"])
        self.assertEqual(self.l.status()["events"],3)
    def test_user_links_rejected_os_aliases_allowed(self):
        from anda.core import private_dir
        # Unresolved temp path on purpose: on macOS it sits under the root-owned /var alias.
        base=Path(self.tmp.name);real=base/"real";real.mkdir();link=base/"link"
        if os.name=="nt":
            subprocess.run(["cmd","/c","mklink","/J",str(link),str(real)],check=True,capture_output=True)
        else:
            os.symlink(real,link)
        with self.assertRaises(AndaError):private_dir(link/"state")
        self.assertTrue(private_dir(real/"state").is_dir())
    def test_watch_worker_survives_a_failed_cycle(self):
        import sqlite3
        from contextlib import redirect_stderr, redirect_stdout
        from anda import cli
        (self.home/"config.json").write_text(json.dumps(self.cfg),encoding="utf-8")
        if os.name!="nt":(self.home/"config.json").chmod(0o600)
        out=io.StringIO()
        with patch.object(cli,"deliver_one",side_effect=sqlite3.OperationalError("database is locked")), \
             patch.object(cli.time,"sleep",side_effect=[None,KeyboardInterrupt]), \
             redirect_stdout(out), redirect_stderr(io.StringIO()):
            code=cli.main(["--home",str(self.home),"worker","--live","--watch","--interval","30"])
        self.assertEqual(code,130)
        self.assertEqual([json.loads(x)["state"] for x in out.getvalue().splitlines()],["cycle_failed","cycle_failed"])
if __name__=="__main__":unittest.main()
