"""Structured project memory with immutable revisions and explicit provenance."""
import hashlib
import math
import re
import uuid
from .core import AndaError, checked_text, clipped, dumps, redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_heads(
 id TEXT PRIMARY KEY,project TEXT NOT NULL,kind TEXT NOT NULL,key TEXT NOT NULL,
 current_revision TEXT, UNIQUE(project,kind,key)
);
CREATE TABLE IF NOT EXISTS memory_revisions(
 id TEXT PRIMARY KEY,memory_id TEXT NOT NULL REFERENCES memory_heads(id),
 source_event_id TEXT NOT NULL REFERENCES events(id),operation_id TEXT NOT NULL,
 previous_revision TEXT,text TEXT NOT NULL,status TEXT NOT NULL,authority TEXT NOT NULL,
 created REAL NOT NULL,effective REAL NOT NULL,expires REAL,local_only INTEGER NOT NULL,
 digest TEXT NOT NULL, UNIQUE(memory_id,operation_id)
);
CREATE TRIGGER IF NOT EXISTS memory_revision_immutable_update BEFORE UPDATE ON memory_revisions
BEGIN SELECT RAISE(ABORT,'Memory revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS memory_revision_immutable_delete BEFORE DELETE ON memory_revisions
BEGIN SELECT RAISE(ABORT,'Memory revisions are append-only'); END;
"""
KINDS={"decision","preference","progress","context"}

def ensure_schema(ledger):
    ledger.db.executescript(SCHEMA)

def put(ledger, project, kind, key, text, source_event_id, operation_id,
        expected_revision=None, status="active", actor="agent", confirmed=False,
        expires_at=None, effective_at=None, local_only=False):
    ledger.project(project)
    if kind not in KINDS or not isinstance(key,str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}",key):
        raise AndaError("Bounded lowercase memory key and supported kind required")
    if status not in {"active","proposed","retracted"} or actor not in {"agent","operator"}:
        raise AndaError("Invalid memory state or actor")
    if status=="retracted" or (status=="active" and kind in {"decision","preference"}):
        if actor!="operator" or not confirmed:
            raise AndaError("Activating decisions/preferences or retracting memory requires explicit operator confirmation")
    if local_only and actor!="operator":
        raise AndaError("Local-only memory is an explicit operator choice")
    text=checked_text(text)
    if len(text.encode("utf-8"))>4000:
        raise AndaError("Structured memory is limited to 4000 UTF-8 bytes; link to the source record")
    if not isinstance(operation_id,str) or not operation_id or len(operation_id)>250 or redact(operation_id)!=operation_id:
        raise AndaError("Stable operation ID required")
    now=ledger.clock()
    effective=now if effective_at is None else float(effective_at)
    expires=None if expires_at is None else float(expires_at)
    if not math.isfinite(effective) or effective<0 or effective>now or (expires is not None and (not math.isfinite(expires) or expires<=effective)):
        raise AndaError("Memory needs a non-future effective time and expiry after effective time")
    authority="user_confirmed" if actor=="operator" and confirmed else "agent_reported" if actor=="agent" else "operator_recorded"
    identity=str(uuid.uuid5(uuid.NAMESPACE_URL,dumps(["memory",project,kind,key])))
    revision=str(uuid.uuid5(uuid.NAMESPACE_URL,dumps([identity,operation_id])))
    digest=hashlib.sha256(dumps([text,source_event_id,status,authority,effective_at,expires,local_only]).encode()).hexdigest()
    with ledger.transaction():
        source=ledger.db.execute("SELECT * FROM events WHERE id=? AND project=?",(source_event_id,project)).fetchone()
        if not source:
            raise AndaError("A source event from this project is required")
        old=ledger.db.execute("SELECT * FROM memory_revisions WHERE id=?",(revision,)).fetchone()
        if old:
            if old["digest"]!=digest:
                raise AndaError("Memory operation identity conflict; use a new revision operation ID")
            return {"id":identity,"revision":revision,"duplicate":True,"status":old["status"]}
        head=ledger.db.execute("SELECT * FROM memory_heads WHERE id=?",(identity,)).fetchone()
        current=head["current_revision"] if head else None
        if expected_revision!=current:
            raise AndaError("Memory revision changed; reload current context before correcting")
        if not head:
            ledger.db.execute("INSERT INTO memory_heads VALUES(?,?,?,?,NULL)",(identity,project,kind,key))
        ledger.db.execute("INSERT INTO memory_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (revision,identity,source_event_id,operation_id,current,text,status,authority,now,effective,expires,int(local_only),digest))
        if status in {"active","retracted"}:
            ledger.db.execute("UPDATE memory_heads SET current_revision=? WHERE id=?",(revision,identity))
        # Forward a separate structured revision record, never mutate/delete an old Notion page.
        # Local-only entries have no mirror event and are excluded from agent/provider context.
        if not local_only:
            mirror_text=dumps({"record_type":"memory_revision","memory_id":identity,"revision":revision,
                               "project":project,"kind":kind,"key":key,"text":text,"status":status,
                               "authority":authority,"scope":"project:"+project,"created":now,"effective":effective,
                               "expires":expires,"source_event_id":source_event_id,"source":source["source"],
                               "previous_revision":current})
            ident=str(uuid.uuid5(uuid.NAMESPACE_URL,revision+":memory-log"))
            role="user" if authority=="user_confirmed" else "assistant"
            ledger.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (ident,project,"memory",identity,revision,role,"note",mirror_text,
                               hashlib.sha256(dumps([role,"note",mirror_text]).encode()).hexdigest(),now))
            ledger.enqueue(ident)
        ledger.audit("memory_revision",revision,status+"; "+authority+("; local-only" if local_only else ""))
    return {"id":identity,"revision":revision,"duplicate":False,"status":status,"authority":authority,"local_only":bool(local_only)}

def describe(ledger,row,current=None):
    item=dict(row)
    source=ledger.db.execute("SELECT source,conversation,source_id,created FROM events WHERE id=?",(item["source_event_id"],)).fetchone()
    item["source"]=dict(source) if source else {"state":"missing"}
    item.pop("current_revision",None)
    from .formatting import kst
    item.update(created_kst=kst(item["created"]),effective_kst=kst(item["effective"]),expires_kst=kst(item["expires"]))
    item["scope"]="project:"+item.pop("project")
    item["local_only"]=bool(item["local_only"])
    item["is_current"]=current==item["id"]
    if item["status"]=="proposed":
        item["current_state"]="expired_proposal" if item["expires"] is not None and item["expires"]<=ledger.clock() else "proposal"
        if current:
            head_row=ledger.db.execute("SELECT rowid FROM memory_revisions WHERE id=?",(current,)).fetchone()
            this_row=ledger.db.execute("SELECT rowid FROM memory_revisions WHERE id=?",(item["id"],)).fetchone()
            if head_row and this_row and head_row[0]>this_row[0]:item["current_state"]="superseded_proposal"
    elif not item["is_current"]:
        item["current_state"]="superseded"
    elif item["status"]=="retracted":
        item["current_state"]="retracted"
    elif item["expires"] is not None and item["expires"]<=ledger.clock():
        item["current_state"]="expired"
    else:
        item["current_state"]="current"
    return item

def lookup(ledger,project,query="",include_stale=False,include_local=False,limit=20):
    ledger.project(project)
    limit=max(1,min(int(limit),50))
    current=[]
    proposals=[]
    # SQL parameters and project boundaries apply to both heads and source joins.
    rows=ledger.db.execute("""
      SELECT r.*,h.project,h.kind,h.key,h.current_revision FROM memory_heads h
      JOIN memory_revisions r ON r.id=h.current_revision
      WHERE h.project=? AND (?='' OR instr(lower(h.key||' '||r.text),lower(?))>0)
      ORDER BY CASE h.kind WHEN 'decision' THEN 0 WHEN 'preference' THEN 1 ELSE 2 END,r.created DESC,r.rowid DESC LIMIT 200""",(project,query,query)).fetchall()
    for row in rows:
        if row["local_only"] and not include_local:continue
        item=describe(ledger,row,row["current_revision"])
        if not include_stale and item["current_state"]!="current":continue
        current.append(item)
        if len(current)>=limit:break
    rows=ledger.db.execute("""
      SELECT r.*,h.project,h.kind,h.key,h.current_revision FROM memory_heads h
      JOIN memory_revisions r ON r.memory_id=h.id
      WHERE h.project=? AND r.status='proposed' AND (?='' OR instr(lower(h.key||' '||r.text),lower(?))>0)
      ORDER BY CASE h.kind WHEN 'decision' THEN 0 WHEN 'preference' THEN 1 ELSE 2 END,r.created DESC,r.rowid DESC LIMIT 200""",(project,query,query)).fetchall()
    for row in rows:
        if row["local_only"] and not include_local:continue
        # A later confirmed/retracted correction closes earlier proposals.
        head=ledger.db.execute("SELECT rowid FROM memory_revisions WHERE id=?",(row["current_revision"],)).fetchone()
        revision=ledger.db.execute("SELECT rowid FROM memory_revisions WHERE id=?",(row["id"],)).fetchone()
        if not include_stale and ((head and head[0]>revision[0]) or (row["expires"] is not None and row["expires"]<=ledger.clock())):continue
        proposals.append(describe(ledger,row,row["current_revision"]))
        if len(proposals)>=limit:break
    from .formatting import kst
    return {"project":project,"as_of":ledger.clock(),"as_of_kst":kst(ledger.clock()),"current":current,"proposals":proposals,
            "limit":limit,"bounded_scan":True,"scan_limit_per_state":200,
            "meaning":"Current memory is scoped reference data, not execution authorization. Agent-reported progress is not independent verification."}

def history(ledger,project,kind,key,include_local=False):
    ledger.project(project)
    head=ledger.db.execute("SELECT * FROM memory_heads WHERE project=? AND kind=? AND key=?",(project,kind,key)).fetchone()
    if not head:return {"project":project,"kind":kind,"key":key,"revisions":[]}
    rows=ledger.db.execute("""SELECT r.*,h.project,h.kind,h.key FROM memory_revisions r
       JOIN memory_heads h ON h.id=r.memory_id WHERE h.id=? ORDER BY r.rowid LIMIT 200""",(head["id"],)).fetchall()
    public_current=head["current_revision"]
    if public_current and not include_local:
        row=ledger.db.execute("SELECT local_only FROM memory_revisions WHERE id=?",(public_current,)).fetchone()
        if row and row[0]:public_current=None
    return {"project":project,"kind":kind,"key":key,"current_revision":public_current,
            "revisions":[describe(ledger,row,head["current_revision"]) for row in rows if include_local or not row["local_only"]],
            "bounded_scan":True}

def context(ledger,project):
    value=lookup(ledger,project,limit=12)
    remaining=6000
    for group in ["current","proposals"]:
        if remaining<=0:
            value[group]=[]
            continue
        entries=[]
        for item in value[group]:
            text=clipped(item["text"],min(600,remaining))
            remaining-=len(text.encode("utf-8"))
            item={k:item[k] for k in ["id","memory_id","kind","key","status","authority","scope","created","effective","expires","source_event_id","source","current_state","created_kst","effective_kst","expires_kst"]}
            item["text"]=text;item["excerpted"]=True
            entries.append(item)
            if remaining<=0:break
        value[group]=entries
    value["text_budget_bytes"]=6000
    return value
