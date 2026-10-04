# Local model catalog preferences

Place `model_catalog.json` beside the active profile's `config.yaml` to curate
WebUI model choices without changing provider discovery or routing:

```json
{"openai-codex": {"add": ["example-model"], "hide": ["retired-model"]}}
```

Keys are canonical provider IDs. `add` and `hide` contain exact bare model IDs.
For Codex, hiding a base ID also hides its `-900k` picker alias; other context
variants are unchanged. This does not revoke provider access or alter existing
main/auxiliary assignments. Explicitly configured models may still be displayed
as configured values by the UI.

Preferences apply to `/api/models` (including session-visit freshness) and
`/api/models/live`, including cached responses. Auxiliary selectors consume
`/api/models`. Changes to the JSON file take effect on the next catalog request;
close/reopen Settings or reload the browser to rebuild existing controls.
Missing or malformed preference files leave provider discovery unchanged.
Named profiles have independent preference files. No provider capabilities,
context limits, or entitlement are inferred by an `add` entry.

The preference file belongs to persistent user state, not the Codex cache. The
supporting Python source must be included in the deployed WebUI image; mounting
only the JSON file into an older image does not implement the feature.
