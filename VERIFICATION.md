# Verification / 검증 기록

Date: 2026-11-18. Candidate: ANDA 0.2.0. Python 3.12.14, Linux.

## Passed local acceptance

- 46 unittest cases cover scoped memory, concurrent deduplication, rollback, outbox independence, leases/fencing, rate limits, ambiguous writes, author spoofing, target/provider changes, redaction, hooks, real CLI/MCP processes and installation.
- New tests cover network-free provisioning plans, new-resource permissions, confirmed creation reuse, unknown creation no-replay, public Notion-parent rejection, preservation of existing live config, native config formats, SSH quoting, 120-event full SQLite snapshot/integrity/permissions, UTC start budget/reset and concurrent reservation, plus Codex/Claude subprocess boundaries.
- Skill Creator quick_validate passed.
- scripts/smoke.py uses actual independent MCP subprocesses with Codex/Claude configuration labels sharing one local ledger. It records once, deduplicates and exports context. It does not invoke the native AI clients or model inference.
- Final ZIP was extracted into a clean path, installed to a new path with spaces/Korean characters and exercised again.
- scripts/check_release.py verifies every manifest hash and rejects selected runtime/private file types, known token patterns and personal-mailbox addresses. A single exact synthetic test credential is allowlisted. This is a limited review, not a complete security audit.

## Live Codex diagnostic: NOT PASSED

The installed official CLI was codex-cli 0.167.0-alpha.4 and its official login-status command reported a ChatGPT login. Raw credentials were not printed or copied.

A new bounded request using ephemeral mode, ignored user config, read-only sandbox and explicitly scoped SQLite/log directories still failed while initializing the in-process app-server:

~~~text
Read-only file system (os error 30)
~~~

No final model answer was obtained. No automatic paid replay occurred. HOME/CODEX_HOME were not overridden and existing provider session files were not requested. A later hardening change also disables selected shell/app/browser/computer/search tools; that invocation structure is fixture-tested, not another successful live inference.

## Simulated or unverified

- Notion/Discord delivery, intake, provisioning and receipts use simulated API transports. ANDA runtime connector tokens were absent.
- Claude Code is not installed here. Its experimental adapter is invocation-tested with a simulated subprocess. Gemini/Grok execution is not implemented; MCP/hooks are their integration path.
- Native client UI registration, genuine cross-provider model continuation, actual remote SSH, macOS/Windows device acceptance and a 48-hour soak remain pending.
- No existing operational bot/channel/role/service was modified by the runtime tests, and no third-party Discord messages were sent.
- MIT was selected for this original implementation. GitHub publication requires an authorized creation session; publication status is reported separately from these checks.

## Repeat

~~~sh
python3 -m unittest discover -s tests -v
python3 scripts/smoke.py
python3 scripts/check_release.py
~~~
