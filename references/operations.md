# Operations / 운영·복구

## State and authority

Store state outside Git/skill directories. POSIX state directory is 0700 and config/database are 0600; symlink components and unrelated nonempty directories are rejected. This is not encryption at rest. Apply a private OS account, disk encryption and Windows ACLs as appropriate.

Only explicit configured projects, destination IDs, intake users and connector environment references are accepted. Queued deliveries retain target_id. Credential rotation with the same target is supported through the environment. Target changes block old backlog; the tool will not move it to a new audience.

Do not copy credentials into prompts or shared memory. Known secret patterns and configured ANDA secret values are redacted before storage, but this is not a guarantee that all PII or all credential formats will be detected. Inspect content before broadening sharing.

## Worker and failures

Record + per-destination outbox writes are atomic. Each send has a lease and fencing token. Confirmed destination receipts are separate from model completion.

- 429/529: Retry-After plus exponential delay, bounded attempts.
- 401/403: blocked; fix permission/authentication, then resolve the confirmed-not-sent record explicitly.
- 400: failed; correct the input/config instead of replaying unchanged requests.
- POST timeout, 5xx, expired running lease: unknown; do not blindly repeat.
- Provider failure/crash/timeout: unknown job; no automatic paid replay.
- Retry one failed destination without re-running an already completed provider.

Run status first. Live worker cycles also perform bounded positive reconciliation (up to five checks per unknown delivery, at least five minutes apart). Run reconcile --live for due positive matches in the exact original target. After the automatic check budget is exhausted, inspect the target and resolve explicitly. Discord matches must be authored by the authenticated bot; Notion child pages must be authored by the connection bot. A missing match is not proof a timed-out write failed. Notion committed_resource_id is retained as a hint, not accepted without scope verification.

Only after inspecting the target can an operator use:

~~~sh
python3 scripts/anda.py --home /private/anda-state resolve \
  --id DELIVERY_ID --receipt VERIFIED_EXTERNAL_ID --evidence "How the matching target record was verified"

python3 scripts/anda.py --home /private/anda-state resolve \
  --id DELIVERY_ID --confirmed-not-sent --evidence "Why non-delivery is confirmed"
~~~

No absolute exactly-once network delivery guarantee is made. Discord nonce deduplication is short-lived. Unknown jobs need manual investigation and, when appropriate, a new explicit request ID; their billing outcome is not inferred.

## First live acceptance

1. Review the pinned source/manifest and configure the private state.
2. Have the user create/select the Discord server and authorize their bot. Configure only a private test channel.
3. Create a Notion internal connection and share a private parent page. Pass tokens through protected environment variables.
4. Run doctor --live (read-only), then explicitly authorized small record → worker --live → actual receipt verification.
5. Enable one intake with an explicit human allowlist; baseline, then submit a new command.
6. Enable Codex only after official sign-in and policy inspection. Verify one actual small output.
7. Test restart, duplicate input, rate limit, partial failure and permission revocation.
8. Test macOS/Windows and a 48-hour soak before relying on unattended operation.

No installer step creates a Discord server, subscribes a web service, changes a firewall, adds remote access, or installs a daemon. Native background-service definitions can be added after the host-specific lifecycle is tested.

## Backup and removal

Stop the foreground worker before copying state. Use SQLite's backup API for an online backup rather than copying just the main DB and omitting WAL. Restore into a private directory and run status/context before resuming live delivery.

The package installer never owns your Notion pages or Discord server, so removing local files does not delete them. Revoke bot/Notion credentials explicitly when disconnecting. Data deletion and retention automation are not implemented in v0.1; do not treat append-only storage as a refusal to honor deletion requirements.


## v0.2 provisioning, budget and backup

provision without --apply is a network-free plan. With --apply, each creation intent is persisted before POST. Confirmed receipt IDs are reused on repeat. Known rejections can be retried manually after fixing credentials/permissions. An unknown creation outcome is deliberately not replayed; inspect the actual resource and preserve the private journal. A dedicated validated receipt-adoption tool remains pending. Do not delete rows to force another POST. Existing live config is not replaced.

Notion child pages inherit parent access. A parent with a public_url is rejected; the API check cannot enumerate every human sharing grant, so verify selected-parent sharing yourself. Discord denies ordinary roles but administrators/server owners retain access. No guild or role is created or modified.

provider.max_calls_per_day defaults to 20 ledger-wide starts per UTC day. Reservation and job claim are one transaction. Failed starts count. This does not cap currency, tokens or provider-internal retries. A pending job whose recorded provider differs from current config is blocked, not rerouted. Stop/inspect the queue and explicitly plan any migration.

backup writes a new private directory outside package/state using SQLite's consistent backup API and checks integrity. It includes all accepted records/outbox/config references. It is not encrypted. Protect it as personal data. For manual restore, stop all workers, verify SNAPSHOT.json hashes, restore into a fresh dedicated private state path with correct OS permissions, disable external connectors/provider/intake until destinations and unresolved leases are reviewed, then inspect status. Do not replay unknown deliveries or jobs solely because a backup was restored.

Multiple devices should connect to one authorized host with SSH stdio. Do not copy active SQLite/WAL files through cloud sync as a distributed database. If the host is offline, MCP/worker is unavailable; no automatic failover is claimed.
