import contextlib
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"runtime"))
from anda.core import AndaError, Ledger, example_config, validate_config
from anda.memory import put, lookup, history
from anda.cli import capture_hook
from anda.connectors import Connectors, DeliveryError
from anda.adapters import external_answer, identity
from anda.engine import work_job
from anda.backup import snapshot
from anda.formatting import kst

ROOT=Path(__file__).resolve().parents[1]

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)/"state"
        self.cfg=example_config("alpha");self.cfg["projects"].append("beta")
        self.now=10000
        self.l=Ledger(self.home,self.cfg,clock=lambda:self.now)
        self.source=self.l.record("alpha","operator","task","source","Explicit source evidence")["id"]
    def tearDown(self):
        self.l.close();self.tmp.cleanup()
    def put(self,**kw):
        args=dict(project="alpha",kind="progress",key="current-task",text="Test evidence recorded, not deployment",
                  source_event_id=self.source,operation_id="rev-1")
        args.update(kw)
        return put(self.l,**args)
    def test_current_memory_provenance_and_kst(self):
        item=self.put()
        current=self.l.context("alpha")["curated_memory"]["current"][0]
        self.assertEqual(current["id"],item["revision"])
        self.assertEqual(current["source"]["source"],"operator")
        self.assertEqual(current["authority"],"agent_reported")
        self.assertEqual(current["scope"],"project:alpha")
        self.assertIn("KST (Asia/Seoul)",current["created_kst"])
    def test_revision_retries_are_idempotent_and_conflicts_explicit(self):
        first=self.put();self.now+=1
        self.assertTrue(self.put()["duplicate"])
        self.assertEqual(len(history(self.l,"alpha","progress","current-task")["revisions"]),1)
        with self.assertRaises(AndaError):self.put(text="Different content")
        self.assertEqual(lookup(self.l,"alpha")["current"][0]["id"],first["revision"])
    def test_correction_preserves_previous_and_rejects_stale_writer(self):
        first=self.put()
        second=self.put(operation_id="rev-2",expected_revision=first["revision"],text="Corrected actual state")
        rows=history(self.l,"alpha","progress","current-task")["revisions"]
        self.assertEqual([x["current_state"] for x in rows],["superseded","current"])
        self.assertEqual(rows[1]["previous_revision"],first["revision"])
        with self.assertRaises(AndaError):
            self.put(operation_id="stale",expected_revision=first["revision"],text="Old agent correction")
        self.assertEqual(lookup(self.l,"alpha")["current"][0]["id"],second["revision"])
    def test_concurrent_correction_has_one_winner(self):
        first=self.put()
        def change(index):
            other=Ledger(self.home,copy.deepcopy(self.cfg),clock=lambda:self.now)
            try:
                return put(other,"alpha","progress","current-task","Concurrent state "+str(index),self.source,"race-"+str(index),expected_revision=first["revision"])["revision"]
            except AndaError:return None
            finally:other.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(change,range(2)))
        self.assertEqual(sum(bool(x) for x in results),1)
        self.assertEqual(len(history(self.l,"alpha","progress","current-task")["revisions"]),2)
    def test_agent_cannot_activate_preferences_or_retract(self):
        with self.assertRaises(AndaError):self.put(kind="preference")
        with self.assertRaises(AndaError):self.put(kind="decision")
        with self.assertRaises(AndaError):self.put(status="retracted")
        proposal=self.put(kind="preference",status="proposed")
        self.assertEqual(lookup(self.l,"alpha")["current"],[])
        self.assertEqual(lookup(self.l,"alpha")["proposals"][0]["id"],proposal["revision"])
    def test_explicit_confirmation_closes_prior_proposal(self):
        self.put(kind="preference",status="proposed")
        active=self.put(kind="preference",operation_id="confirmed",actor="operator",confirmed=True)
        found=lookup(self.l,"alpha")
        self.assertEqual(found["proposals"],[])
        self.assertEqual(found["current"][0]["authority"],"user_confirmed")
        self.assertEqual(found["current"][0]["id"],active["revision"])
        self.assertEqual(len(history(self.l,"alpha","preference","current-task")["revisions"]),2)
    def test_expired_memory_excluded_without_erasing_history(self):
        self.put(expires_at=self.now+1)
        self.now+=2
        self.assertEqual(lookup(self.l,"alpha")["current"],[])
        self.assertEqual(lookup(self.l,"alpha",include_stale=True)["current"][0]["current_state"],"expired")
        self.assertEqual(history(self.l,"alpha","progress","current-task")["revisions"][0]["current_state"],"expired")
    def test_retraction_excludes_current_preserves_old(self):
        first=self.put()
        self.put(operation_id="withdraw",status="retracted",actor="operator",confirmed=True,expected_revision=first["revision"],text="Withdrawn after checking")
        self.assertEqual(lookup(self.l,"alpha")["current"],[])
        rows=history(self.l,"alpha","progress","current-task")["revisions"]
        self.assertEqual([x["current_state"] for x in rows],["superseded","retracted"])
    def test_project_source_and_query_boundaries(self):
        self.put()
        self.assertEqual(lookup(self.l,"beta")["current"],[])
        self.assertEqual(history(self.l,"beta","progress","current-task")["revisions"],[])
        with self.assertRaises(AndaError):self.put(project="beta")
        with self.assertRaises(AndaError):lookup(self.l,"outside")
        self.assertEqual(lookup(self.l,"alpha",query="' OR 1=1 --")["current"],[])
    def test_local_only_never_enters_shared_context_or_outbox(self):
        self.cfg["destinations"]["notion"].update(enabled=True,parent_page_id="a"*32,memory_page_id="b"*32)
        self.l.config=copy.deepcopy(self.cfg)
        before=self.l.status()["events"]
        self.put(actor="operator",local_only=True,text="Private sensitive memory retained only here")
        self.assertEqual(self.l.status()["events"],before)
        self.assertEqual(self.l.status()["deliveries"],[])
        self.assertNotIn("Private sensitive",json.dumps(self.l.context("alpha")))
        self.assertEqual(history(self.l,"alpha","progress","current-task")["revisions"],[])
        self.assertEqual(len(lookup(self.l,"alpha",include_local=True)["current"]),1)
    def test_revision_rows_are_append_only(self):
        item=self.put()
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("UPDATE memory_revisions SET text='tamper' WHERE id=?",(item["revision"],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("DELETE FROM memory_revisions WHERE id=?",(item["revision"],))
    def test_memory_mirror_uses_frozen_separate_notion_target(self):
        self.cfg["destinations"]["notion"].update(enabled=True,parent_page_id="a"*32,memory_page_id="b"*32)
        self.l.config=copy.deepcopy(self.cfg)
        self.put()
        delivery=self.l.status()["deliveries"][0]
        self.assertEqual(delivery["route"],"memory")
        self.assertEqual(delivery["target_id"],"b"*32)
        changed=copy.deepcopy(self.cfg);changed["destinations"]["notion"]["memory_page_id"]="c"*32
        with self.assertRaises(DeliveryError):Connectors(changed).check_target(delivery)
    def test_memory_snapshot_keeps_all_revisions(self):
        first=self.put();self.put(operation_id="correct",expected_revision=first["revision"],text="New")
        target=Path(self.tmp.name)/"snapshot";snapshot(self.l,target)
        with contextlib.closing(sqlite3.connect(target/"ledger.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM memory_revisions").fetchone()[0],2)
    def test_curated_context_text_budget(self):
        for index in range(20):
            self.put(key="item-"+str(index),operation_id="item-"+str(index),text="가"*1000)
        memory=self.l.context("alpha")["curated_memory"]
        total=sum(len(x["text"].encode()) for x in memory["current"]+memory["proposals"])
        self.assertLessEqual(total,6000)
        self.assertTrue(all(x["excerpted"] for x in memory["current"]))
    def test_invalid_effective_times_fail_before_storage(self):
        for kw in [dict(expires_at=self.now),dict(expires_at=float("nan")),dict(effective_at=self.now+1)]:
            with self.assertRaises(AndaError):self.put(**kw)
    def test_generic_visible_events_need_stable_ids_and_scope(self):
        payload={"schema":"anda.event.v1","event_type":"assistant_message","conversation_id":"other-client","event_id":"reply1","text":"Visible answer"}
        first=capture_hook(self.l,"alpha","qwen",payload)
        self.assertTrue(first["persisted"])
        self.assertTrue(capture_hook(self.l,"alpha","qwen",payload)["duplicate"])
        self.assertEqual(self.l.context("alpha")["events"][-1]["source"],"qwen")
        payload.pop("event_id")
        with self.assertRaises(AndaError):capture_hook(self.l,"alpha","qwen",payload)
    def test_external_protocol_is_real_subprocess_not_model_inference(self):
        helper=Path(self.tmp.name)/"adapter.py"
        helper.write_text('import json,sys,os\nr=json.load(sys.stdin)\nassert r["schema"]=="anda.agent.request.v1"\nassert "ANDA_DISCORD_TOKEN" not in os.environ\nprint(json.dumps({"schema":"anda.agent.response.v1","answer":"Protocol roundtrip"}))\n')
        spec=dict(name="external",label="example",argv=[sys.executable,str(helper)],pass_env=[])
        self.cfg["provider"].update(spec);validate_config(self.cfg)
        answer=external_answer("Visible request",str(self.home),10,self.cfg["provider"])
        self.assertEqual(answer,"Protocol roundtrip")
        previous=identity(self.cfg["provider"])
        self.cfg["provider"]["argv"].append("changed")
        self.assertNotEqual(previous,identity(self.cfg["provider"]))
    def test_external_config_blocks_connector_env_and_argument_secrets(self):
        self.cfg["provider"].update(name="external",label="example",argv=[sys.executable,"adapter.py"],pass_env=["ANDA_DISCORD_TOKEN"])
        with self.assertRaises(AndaError):validate_config(self.cfg)
        self.cfg["provider"].update(argv=["python3","adapter.py"],pass_env=[])
        with self.assertRaises(AndaError):validate_config(self.cfg)
    def test_kst_user_display_and_utc_storage(self):
        self.assertEqual(kst(0),"1970-01-01 09:00:00 KST (Asia/Seoul)")
        self.assertEqual(self.l.context("alpha")["as_of"],self.now)
        self.assertIn("KST",self.l.context("alpha")["as_of_kst"])
    def test_memory_mcp_from_another_generic_agent(self):
        cfg=self.home/"config.json";cfg.write_text(json.dumps(self.cfg));cfg.chmod(0o600)
        base=[sys.executable,str(ROOT/"scripts/anda.py"),"--home",str(self.home),"mcp","--agent","custom-ai","--project","alpha"]
        messages=[
            {"jsonrpc":"2.0","id":1,"method":"initialize"},
            {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"memory_put","arguments":{"kind":"preference","key":"language","text":"Inferred preference, needs user confirmation","source_event_id":self.source,"operation_id":"mcp-proposal"}}},
            {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"memory_lookup","arguments":{}}},
        ]
        result=subprocess.run(base,input="\n".join(json.dumps(m) for m in messages)+"\n",text=True,capture_output=True,timeout=15,check=True)
        rows=[json.loads(x) for x in result.stdout.splitlines()]
        saved=json.loads(rows[1]["result"]["content"][0]["text"])
        found=json.loads(rows[2]["result"]["content"][0]["text"])
        self.assertEqual(saved["status"],"proposed")
        self.assertEqual(found["current"],[])
        self.assertEqual(found["proposals"][0]["text"],"Inferred preference, needs user confirmation")
