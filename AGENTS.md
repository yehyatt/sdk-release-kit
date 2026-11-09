# AGENTS.md

Guidance for AI coding agents (and humans) changing this repository.

## Hard rules

1. **No absolute machine paths** in `recipes/`, `examples/` or Python defaults. Recipes use
   paths relative to the SDK checkout. Absolute paths live only in user settings
   (`~/.config/sdk-release-kit/`). Defaults may use `~`, which is portable.
2. **No secrets in git.** Never commit keystores, `.p8` files, Play service account JSON,
   tokens, passwords or card numbers, and never write them into examples or tests. Keys are
   configured through the Settings UI or the user data directory, and passwords are referenced
   (`SecretRef`: keychain or env), never stored.
3. **Never log a secret.** Anything resolved from a `SecretRef` must be registered with
   `RunLog.add_secret()` before it can reach a log line. Prefer environment variables over
   command-line arguments for secret values.
4. **Keep settings backward compatible.** Settings models use `extra="allow"`; add fields with
   defaults, never rename or remove without a migration and a `schema_version` bump.
5. **Destinations soft-skip.** Missing Play or TestFlight credentials must produce a "skipped"
   result with a reason, not a failed run.

## Where things are

| Path | Purpose |
| --- | --- |
| `recipes/sdks.yaml` | Portable recipes and shared-folder target ids |
| `examples/*.example.yaml` | Templates copied by `sdk-release-kit init` |
| `src/sdk_release_kit/models.py` | Pydantic schemas for recipes and settings |
| `src/sdk_release_kit/paths.py` | User data dir, env overrides, `safe_join` |
| `src/sdk_release_kit/store/` | One module per settings layer (repos, outputs, keys, custom) |
| `src/sdk_release_kit/recipes.py` | Recipe loading, overlays, `${...}` substitution |
| `src/sdk_release_kit/builders/` | Step handlers per platform; `base.py` runs and collects |
| `src/sdk_release_kit/uploaders/` | Shared folder, Google Play, TestFlight |
| `src/sdk_release_kit/pipeline.py` | Select, build, publish, report |
| `src/sdk_release_kit/server.py` | Settings UI API (FastAPI) |
| `src/sdk_release_kit/cli.py` | Typer CLI |
| `web/` | Static Settings UI (plain HTML, CSS, JS; no build step) |

## Common changes

**Add an SDK.** Add a recipe in `recipes/sdks.yaml` using existing step types, plus any new
`onedrive_targets`. No code change needed. Run `sdk-release-kit recipes` and
`sdk-release-kit build --dry-run --sdks <id>` to check it.

**Add a step type or platform.**
1. Add a model class to `models.py` (subclass `_StepBase`, `type: Literal["name"]`) and add it
   to the `Step` union.
2. Write a handler in `builders/<platform>.py` decorated with `@handler("name")` that returns a
   `StepPlan` (commands, optional `prepare`/`finish`, extra collect roots).
3. Import the module in `builders/__init__.py`.
4. Document it in the step table in `README.md`.

**Add a destination.** Add an uploader module with `readiness()` and an upload function, add the
name to `Destination`/`DESTINATIONS` in `models.py`, add an Outputs model section, and wire it
into `Pipeline.destination_status` and `Pipeline._upload`.

**Read a new custom value.** Reference it from a recipe as `${custom.your.key:-default}`. Do not
hardcode team ids, bundle ids or package names in Python.

## Working style

* Small, focused diffs. Keep the UI dependency free.
* Verify changes with `sdk-release-kit build --dry-run`, then a real build of one recipe into
  a scratch staging directory (`SDK_RELEASE_KIT_HOME=/tmp/srk sdk-release-kit init`).
* Run `ruff check src` before committing.
