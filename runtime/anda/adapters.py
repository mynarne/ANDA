"""Versioned external-adapter protocol. Operator-selected argv only; no shell."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from .core import AndaError, checked_text, dumps

def identity(spec):
    if spec["name"]!="external":return spec["name"]
    selected={k:spec.get(k) for k in ["label","argv","pass_env"]}
    return "external:"+spec["label"]+":"+hashlib.sha256(dumps(selected).encode()).hexdigest()[:24]

def external_answer(prompt, home, timeout, spec):
    # Native session/token stores are never inspected by ANDA.
    # A reviewed adapter may itself use its provider's supported login or API.
    base={"PATH","HOME","USERPROFILE","APPDATA","LOCALAPPDATA","SYSTEMROOT","LANG","LC_ALL","TMPDIR","TEMP","TMP"}
    env={k:v for k,v in os.environ.items() if k in base or k in spec.get("pass_env",[])}
    with tempfile.TemporaryDirectory(prefix="adapter-",dir=home) as work:
        payload={"schema":"anda.agent.request.v1","prompt":prompt,"constraints":{"text_response_only":True,"verification_required":True}}
        try:
            with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as diagnostic:
                result=subprocess.run(spec["argv"],input=dumps(payload).encode("utf-8"),stdout=output,stderr=diagnostic,
                                      cwd=work,env=env,timeout=timeout,check=False)
                if result.returncode:
                    raise AndaError("External adapter failed; raw diagnostics suppressed")
                output.seek(0);raw=output.read(64001)
            if len(raw)>64000:raise AndaError("External adapter response exceeded protocol limit")
            try:response=json.loads(raw)
            except (ValueError,UnicodeError):raise AndaError("External adapter must return one JSON response") from None
            if not isinstance(response,dict) or response.get("schema")!="anda.agent.response.v1" or set(response)-{"schema","answer","provider_label"}:
                raise AndaError("Unsupported external adapter response")
            return checked_text(response.get("answer"))
        except subprocess.TimeoutExpired:
            raise AndaError("External adapter timed out; model execution outcome unknown, no automatic replay") from None
