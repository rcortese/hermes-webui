# Deployment extensions in this fork

This fork carries a small set of deployment-oriented extensions on top of Hermes WebUI `exp-v0.52.379`. They support installations where one WebUI presents local profiles alongside profiles owned by separate Hermes Gateway instances.

These features are optional. A normal single-instance installation continues to use local, in-process chat unless Gateway mode or a remote profile proxy is explicitly configured.

## Remote profile proxies

A remote profile proxy is a selector entry backed by a different Hermes Gateway. It is a routing identity, not a local profile directory. Selecting it routes new turns to its configured Gateway and remote profile.

Configure proxies under `webui.profile_proxies`:

```yaml
webui:
  profile_proxies:
    remote-operator:
      label: Remote operator
      base_url: https://gateway.example.internal:8642
      api_key_env: REMOTE_OPERATOR_GATEWAY_KEY
      remote_profile: operator
      session_key_prefix: webui:remote-operator
```

The API key should be supplied through the named environment variable. Do not put credentials in a public configuration file; use your deployment's secret or environment-management facility.

The equivalent environment-only form is:

```bash
HERMES_WEBUI_PROFILE_PROXY_REMOTE_OPERATOR_BASE_URL=https://gateway.example.internal:8642
HERMES_WEBUI_PROFILE_PROXY_REMOTE_OPERATOR_API_KEY_ENV=REMOTE_OPERATOR_GATEWAY_KEY
HERMES_WEBUI_PROFILE_PROXY_REMOTE_OPERATOR_REMOTE_PROFILE=operator
HERMES_WEBUI_PROFILE_PROXY_REMOTE_OPERATOR_SESSION_KEY_PREFIX=webui:remote-operator
```

The token between `PROFILE_PROXY_` and `_BASE_URL` becomes the selector name; underscores are normalized to hyphens.

### Routing and safety behavior

- Proxy URLs must use HTTP or HTTPS and include a hostname. Userinfo, query strings, fragments, malformed ports, and other schemes are rejected.
- A selected remote proxy without its own credential fails closed. WebUI never falls back to a local profile or reuses the local Gateway credential.
- A case-insensitive collision between a local profile and a remote proxy is rejected for execution. The selector suppresses the duplicate local row and exposes only sanitized host-level diagnostics; the collision still fails closed when execution is requested.
- Gateway credentials and session identity are sent only to the configured endpoint. Credentialed health and chat requests do not follow redirects.
- Selector payloads expose only sanitized host information. They do not include the API key, complete base URL, URL path, query, or fragment.
- Remote proxy chat remains subject to the Gateway compatibility limits described in [Advanced chat setup](advanced-chat-setup.md).

Cron operations are intentionally unsupported while a remote proxy is selected. WebUI returns `remote_cron_proxy_unsupported` before reading or modifying local cron state. Manage those jobs through the owning remote installation instead.

## Profile selector order

The profile selector uses a stable, case-insensitive order:

1. the active `moss` local profile, when present;
2. remote profile proxies;
3. the local `default` profile;
4. remaining local profiles.

Rows inside each group are sorted by normalized name. The active row remains active after ordering.

## Service-authorized session launch

A trusted internal service can create a persistent local-profile WebUI session and start its first turn through:

```text
POST /api/internal/session-launch
X-Hermes-Service-Token: <service token>
Content-Type: application/json
```

The route is documented because it is part of this public source contract; its path is not an authentication factor. It is not a general browser API. The exact POST route uses service-token authority instead of browser-cookie and CSRF authority. Nearby paths and other HTTP methods do not inherit that exemption.

Request body:

```json
{
  "profile": "moss",
  "workspace": "<approved-workspace>",
  "model": "<provider>/<model>",
  "reasoning": "profile_default",
  "initial_prompt": "Inspect the supplied candidate and report the result."
}
```

The token comes from `HERMES_WEBUI_SESSION_LAUNCH_TOKEN` and must contain at least 32 non-whitespace characters. Required fields are `profile`, `workspace`, and `initial_prompt`. `model` is optional. `reasoning`, when supplied, must be `profile_default`. Extra fields are rejected, and the prompt is capped at 24,000 characters.

The route accepts local profiles only, resolves the workspace through the normal trusted-workspace boundary, persists the session, starts the turn, and returns HTTP `201` only after the exact session and stream are verified active. The response contains `session_id`, `stream_id`, the resolved profile, workspace and model, plus compact verification state; it never echoes the token or prompt.

This endpoint creates work. Restrict it to trusted service callers and a private network boundary. Merely setting the token does not schedule or invoke anything. Nearby paths and other HTTP methods must not inherit this service-token exemption.

## Gateway telemetry and context display

When `/health/detailed` is unavailable but the basic remote health endpoint succeeds, WebUI reports the Gateway as reachable with degraded telemetry. The UI does not offer a restart action for that state because reachability itself is healthy.

Gateway-backed usage shows a context percentage only when a positive context-window length is available. If only prompt-token usage is known, WebUI displays the token count and marks the context window as unknown rather than assuming a 128K window.

## Session titles

Automatic title prompts prioritize the conversation's substantive topic and intent over workflow words such as “audit,” “review,” “handoff,” or a tool name. Workflow terminology remains eligible when it is itself the central subject. Existing language, length, and title-only output guards remain in force.

## `no response` compatibility bridge

The `v0.52.113` transcript evaluator can still misclassify a successful turn as “no response” when the current user message was already checkpointed at the durable transcript boundary. This fork recognizes only the exact matching final display-tail user as the checkpointed current turn.

The bridge deliberately does not treat an arbitrary older same-text user row as the current turn. That negative case remains terminal so a replayed historical assistant message cannot satisfy a new retry.

The prerelease retains upstream's explicit active-turn identity as authoritative. The compatibility bridge applies only when no explicit identity exists; it recognizes an exact checkpointed display-tail user, never an older same-text user. Positive, historical-negative and explicit-new-identity regressions cover these boundaries.

## Prerelease integration

The upstream `legacy` local-worker backend tag remains unchanged: cancellation and Steer use it as an ownership discriminator. Gateway admission idempotency, restart reattachment, approval capability negotiation, profile-aware cache invalidation, and regeneration transactions are retained. Remote target resolution also reaches regeneration and restart reattachment. Approval events prefer Agent `request_id`; replies carry both `request_id` and the compatibility `approval_id`.

Copy conversation links produce reference data ready to paste into another session:

```text
[Storage migration](https://webui.example/session/abc123) · `@session:moss/abc123`
```

The compact, single-line reference keeps the title clickable for people; the distinct internal locator identifies the profile and conversation without making the URL a retrieval instruction. This is not a public share and does not grant access. The conversation's profile takes precedence over the active selector, with `default` as the last fallback. Titles are escaped, identifiers are URL-encoded, and the WebUI mount subpath is retained; query parameters (including PWA launch metadata) and fragments are omitted. Clipboard rejection uses the legacy copy fallback and reports failure rather than false success if both methods fail.

Paste your new request alongside the reference. Title-generation prompts prioritize that new substantive intent; the old title is context and only a fallback when no new topic is supplied. This guides title generation, not the agent's tools or policies, and cannot guarantee what a model will choose to retrieve.

## Deployment-selected Moss memory integration

This common source line carries profile-local model picker curation (see [local-model-catalog.md](local-model-catalog.md)) and the authenticated browser memory bridge from the Moss image. The process-start environment variable `HERMES_WEBUI_MOSS_MEMORY_GATE` selects only this technical integration:

- `enabled` (also the default when unset): require the existing `agent.moss_memory_gate` module. Moss deployments must use this mode. Missing/broken imports prevent route loading; there is no automatic ungated fallback.
- `disabled`: explicitly select the pre-existing non-memory-bridge behavior for deployments such as Roy with Agent 0.21.4, which does not carry that module. WebUI does not import the gate, capture browser memory admission, or sign memory proofs. Stale memory proof headers are removed before Runs API submission; ordinary Gateway authentication/session headers remain unchanged.

Only those exact values are accepted; empty or unknown values fail startup. This is a process-wide deployment setting, not a per-profile/browser setting, and requires a restart to change. Do not disable it to work around a broken Moss deployment. It does not select an identity, modify memory rules, copy policy/keys, or alter Agent-side memory behavior. For Docker, set it explicitly in the WebUI service's `environment` (a Compose `.env` alone does not inject arbitrary variables). No runtime configuration is changed by merging this source.

Password login records its authentication type. Only the gate's admitted browser context, a local Gateway target, source `webui`, and a non-goal turn can forward memory admission. Normal and regenerated turns use the same restriction. Service launches, remote proxies, cron/goals, and restart re-admission cannot mint browser authority. Signing happens over the exact serialized Runs API request while retaining upstream's `Idempotency-Key`. Neither the memory proof nor the browser admission is persisted in the Gateway restart record. Gate policy, key custody and the receiving Agent remain external deployment prerequisites.

## Validation and activation boundary

The fork-specific contracts have focused tests for:

- remote/local target resolution and collision rejection;
- credential isolation and redirect refusal;
- selector sanitization and ordering;
- remote cron fail-closed behavior;
- service-launch authentication, input validation, persistence, and active-stream verification;
- measured versus unknown context display;
- degraded remote health handling;
- topic-first title prompts;
- successful checkpointed turns and historical same-text negative controls.

Publishing this source does not configure a proxy, create a service token, build an image, update Compose, restart WebUI, or activate a runtime. Runtime activation is a separate, explicitly authorized operational procedure.
