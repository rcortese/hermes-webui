# Main / production convergence

The upgraded production line b95e7cd0bb4a736cb4206367712d65e5fa805f33 is merged with the prior fork main fe0035d720c234ba44641dfddd2620b368201df3. The production port was based on upstream exp-v0.52.408; restoring complete old main files would regress upstream replay, admission rollback and title language handling.

The production port retained the main fork's shared OAuth/auth home and principal isolation, remote profile ownership/approval request identities, authenticated memory gates, local model catalog, service-session launch, selectors, topic-first copied-conversation titles and UI links. Clean automatic merge entries are identical to production; conflicted old implementation hunks retain the adapted production implementation, with focused auth, approval, gateway, memory and copied-title regression tests.

The existing live Moss-only Runs admission alias is incorporated into api/routes.py: only the local gateway transport http://moss:8648 with source webui, non-goal execution, Moss identity and the webui session prefix receives the default single-home profile alias. Browser admission remains Moss and its ContextVar is never mutated.

The historical X-Hermes-Delegate-Mode: inline header is retired and is absent from both active gateway request paths. Do not reapply the historical overlay. Retiring the header does not implement completion wake.

No user sessions, settings, OAuth stores, memory state or attachments are replaced by source convergence. Runtime activation is separately coordinated.
