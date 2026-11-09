---
name: release-sdks
description: Build mobile SDK demo apps or SDK packages (Android, iOS, Flutter, React Native) and publish them to a shared OneDrive folder, Google Play internal testing or TestFlight with SDK Release Kit. Use when the user asks to build, release, ship, upload or distribute SDK demos or packages, refresh the device-lab shared folder, push a demo to TestFlight or Play internal, or check the last SDK release run.
---

# Release SDKs with SDK Release Kit

Use the `sdk-release-kit` MCP tools. If they are not available, use the CLI (`uvx sdk-release-kit ...`).

## Steps

1. **Check readiness.** Call `get_status`. It reports which recipes have a checkout, which keys are configured (never their values) and which destinations are ready.
   - If no settings exist, tell the user to run `uvx sdk-release-kit init` and `uvx sdk-release-kit serve`, then fill Repositories, Outputs and Keys in the Settings UI.
   - If a recipe has no checkout and the user tells you where it is, call `set_repo_path`.
   - Never ask the user to paste passwords, keystore passwords or API keys into the chat. Keys belong in the Settings UI or `uvx sdk-release-kit keys set <name>`.
2. **Pick recipes.** Call `list_recipes` and map the user's words to recipe ids (for example "Android current" to `android-current`). Ask only if it is truly ambiguous.
3. **Plan.** Call `plan_build` with the recipes, `artifacts` (`demos`, `packages` or `both`) and `destinations`. Show the user the destinations that are ready or skipped and anything marked ERROR.
4. **Build.** Call `run_build` only when the user asked for a build or release. Say it can take several minutes. Uploads to Google Play or TestFlight reach real testers, so confirm before including `play` or `testflight` unless the user named them.
5. **Report.** Summarise per recipe: status, version, artifacts and where each went (shared-folder path, Play track, TestFlight). For failures, call `get_run_report` and quote the relevant log lines.

## Notes

- Missing Play or TestFlight credentials are a soft skip, not a failure.
- Legacy recipes usually share store ids with current ones and only go to the shared folder.
- Recipe changes belong in `recipes/sdks.yaml` (shared) or `<settings>/recipes.d/*.yaml` (this machine only).
