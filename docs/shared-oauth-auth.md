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
metadata and ordering are retained. A positively matched DEAD row is released
only when its quarantine records a terminal authentication reason or 401, and
not 429. Its terminal status/error fields are cleared after the fresh grant is
accepted. Other rows and genuine exhaustion/quota cooldowns remain unchanged.

Before any device-code request, onboarding captures the effective auth path and
the existing principals' IDs and exact access/refresh pairs under `auth.lock`.
After exchange it locks and rereads again, checking only the returned principal
(account ID **and** subject) against its prior row, including expected absence
for a new principal. The legacy singleton's prior pair/absence is checked too.
A changed target row, singleton or auth root fails without writing tokens; start
a new login rather than overwrite a concurrent login/refresh. Peer token or
metadata changes do not invalidate the target's expectation. No timestamps are
used to choose a token generation, and no OAuth network call holds `auth.lock`.
The private snapshot never enters public start/poll payloads and is dropped on
terminal transitions.

## Caller inventory and compatibility boundary

The production Codex exchange path in this checkout is:
`static/onboarding.js` → `POST /api/onboarding/oauth/start` in `api/routes.py`
→ `start_onboarding_oauth_flow` → `_request_codex_user_code` → spawned
`_run_codex_oauth_worker` → `_poll_codex_authorization` →
`_exchange_codex_authorization` → `_persist_codex_credentials(expected_state=...)`.
The `start_codex_device_code` compatibility shim delegates to that same start
path, so it also captures before the first network request. `poll_codex_token`
is an error-only stub, not another exchange/persistence implementation.

Repository callsite inspection found no production caller of the legacy
`_save_codex_credentials` wrapper. It and direct persistence tests retain
snapshot-less, locked fresh-grant replacement; this is **not** a prior-state CAS
and must not be used to implement another operational login/exchange path.
No active synchronous login path was found to excuse from CAS. The regression
inventory guards the API persistence callers; behavioral tests drive both start
entrypoints, with conflicts during device-code request and token exchange.
These are checkout-bound findings, not claims about unknown external importers.

Six synthetic profile homes sharing one auth root are tested with two principals
(`rcortese`, `viviane`): all snapshots/writes use the same root lock, the first
same-principal login succeeds and stale peer flows fail without writes. No local
auth copies, new singleton, or broker are introduced. This does not verify the
six live persona mounts or cross-host filesystem locking.

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
