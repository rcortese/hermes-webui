# Shared Codex OAuth storage

`HERMES_AUTH_HOME` selects the root containing `auth.json` for Codex OAuth
login, onboarding status, credential-pool configuration and streaming self-heal.
When unset or whitespace-only, these paths use the active Hermes profile home.
The override does not change profile identity, configuration, sessions, memory,
skills or `.env` API keys. It does not relocate Claude Code's external credential
store or the Anthropic credential-linking marker.

Codex login uses the core auth-store lock and atomic private writer. A successful
login updates only the row matching both trimmed JWT account ID and subject,
or appends a new principal after existing rows. JWT decoding is routing only,
not signature or grant validation. Existing IDs, labels, priorities, creation
metadata, ordering and cooldown/error fields are retained. Login does not reset
quota cooldowns or turn an exhausted account into an available one.

Rows written by login use `manual:device_code` so the core's per-row refresh path
cannot adopt legacy singleton tokens. A legacy Codex singleton is removed only
when its access-token claims positively identify the same principal as the new
login, in the same locked write that saves the pool row. No new singleton is
created. Other provider state and other accounts are retained.

A different, unknown or malformed legacy singleton causes login to fail without
changing the store, even for an empty pool. This prevents duplicate singleton
seeding and cross-account token adoption. Diagnose and reconcile that legacy
state separately; do not delete an unidentified account to force login through.
Malformed pools, unidentified existing rows and duplicate principals likewise
fail closed. Opaque tokens are accepted only for an empty local pool without a
shared override or legacy singleton; later login cannot safely identify that row.

This behavior requires a Hermes core supporting `HERMES_AUTH_HOME`, explicit
`target_path` locking/writing and independent `manual:device_code` refresh.
Offline regression tests use synthetic grants and temporary stores; they prove
storage/selection behavior, not live provider authentication or deployment.
