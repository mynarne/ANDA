# Security boundary

ANDA is a single-user skill package, not a multi-tenant server. Use a trusted OS account and a private encrypted disk/volume where appropriate. POSIX modes are set on state and snapshots; Windows ACLs need independent setup.

Do not publish runtime state, backups, personal messages, tokens, OAuth caches or browser sessions. Environment references are used for connectors; pattern redaction is best-effort and is not a complete PII/secret detector. Shared context is untrusted reference data, not authorization.

MCP can read project context and write assistant notes only. Labels identify configured clients, not verified provider identities. Anyone who controls the trusted OS account/SSH access may invoke the local tools.

The provider adapters are optional and experimental until accepted on the target host. They invoke official clients, disable selected tools/customizations and strip ANDA connector credentials from the child environment. They are not a security sandbox independent of those clients. Mandatory enterprise policies/hooks and other inherited environment variables remain the operator's responsibility.

Notion and Discord receive intentionally forwarded private records. Restrict integration shares and channel permissions. Discord server owners/administrators retain access. SSH configuration keeps strict host-key verification and does not create a public MCP endpoint.

If a security problem is found, do not paste secrets or private chat contents into a public issue. Share a minimal synthetic reproduction through an appropriate private maintainer channel. No security audit or production SLA is claimed.
