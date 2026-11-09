# SDK Release Kit

Build mobile SDK demo apps and SDK packages from one or more local checkouts, then publish
them to a shared folder (OneDrive or any synced folder), Google Play internal testing and
TestFlight. Configure it once per machine through a local Settings UI; the same repository
works on every developer's Mac without editing scripts.

Supports Android (Gradle), iOS (Xcode), Flutter and React Native SDKs out of the box. New
SDKs are added in a YAML recipe file, usually without code changes.

## Install

Python 3.11 or newer.

```bash
git clone https://github.com/yehyatt/sdk-release-kit.git
cd sdk-release-kit
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # add '.[play]' for direct Google Play API uploads
sdk-release-kit init        # creates your settings from examples/
sdk-release-kit serve       # opens the Settings UI on http://127.0.0.1:8765
```

Fill in **Repositories**, **Outputs** and **Keys** in the UI, then:

```bash
sdk-release-kit build --sdks flutter,android-current --artifacts both --dest onedrive
```

## Commands

| Command | What it does |
| --- | --- |
| `sdk-release-kit init [--force]` | Copy `examples/*.example.yaml` into your settings directory |
| `sdk-release-kit serve` | Settings UI (Repositories, Outputs, Keys, Custom data, Run) |
| `sdk-release-kit build ...` | Build and publish (see options below) |
| `sdk-release-kit build --dry-run` | Print the plan: commands, collected files, destinations |
| `sdk-release-kit recipes` | List recipe ids and where they were loaded from |
| `sdk-release-kit status` | Settings files, key status (never values), installed tools |
| `sdk-release-kit keys set <name>` | Store a secret in the OS keychain (prompted, not echoed) |

`build` options:

* `--sdks android-current,flutter`: recipe ids. Default: every enabled repository.
* `--artifacts demos|packages|both`
* `--dest onedrive,play,testflight`: default is every destination enabled in Outputs.
* `--dry-run`: plan only. `--skip-upload`: build only. `--json`: print the report.

## How configuration is split

| Layer | Where | In git? | Holds |
| --- | --- | --- | --- |
| Recipes | `recipes/sdks.yaml` | Yes | Relative paths inside a repo, Gradle tasks, Xcode schemes, shared-folder target ids, store flags |
| Repos | `<settings>/repos.yaml` | No | Recipe id to absolute checkout path; JDK, Android SDK, Flutter, Xcode locations |
| Outputs | `<settings>/outputs.yaml` | No | Staging directory, shared folder root, Play and TestFlight options |
| Keys | `<settings>/keys.yaml` | No | Paths to key files and *references* to passwords (keychain or env) |
| Custom | `<settings>/custom.yaml` | No | Free-form YAML read by recipes as `${custom.key}` |

`<settings>` is `~/.config/sdk-release-kit/` (or `$XDG_CONFIG_HOME/sdk-release-kit`, or
`%APPDATA%\sdk-release-kit` on Windows). Overrides:

* `SDK_RELEASE_KIT_HOME`: use another settings directory
* `SDK_RELEASE_KIT_REPOS`, `SDK_RELEASE_KIT_OUTPUTS`, `SDK_RELEASE_KIT_KEYS`, `SDK_RELEASE_KIT_CUSTOM`: point one layer at a specific file
* `SDK_RELEASE_KIT_RECIPES`: an extra recipe file merged over the shipped one

Machine-specific recipe tweaks go in `<settings>/recipes.d/*.yaml`. They are deep-merged over
`recipes/sdks.yaml`, so you can change one scheme name without forking the file.

## Multi-device setup

1. Clone the repository and `pip install -e .` on the new machine.
2. `sdk-release-kit init`, or copy your `<settings>` directory from another machine and fix
   the absolute paths.
3. Open `sdk-release-kit serve` and check that every repository shows a green check and every
   key shows "configured".
4. Run `sdk-release-kit build --dry-run` before the first real build.

## Destinations

| Artifact | Shared folder | Google Play | TestFlight |
| --- | --- | --- | --- |
| Demo apps (APK, AAB, IPA, simulator `.app`) | Yes | AAB, when the recipe sets `store.play` | IPA, when the recipe sets `store.testflight` |
| SDK packages (AAR zip, XCFramework zip, `npm pack` tarball, Flutter source zip) | Yes | No | No |

* **Shared folder.** Treated as a locally synced directory: files are copied in (written to a
  temporary name, then renamed). Demos land at `<root>/<demos folder>/<target name>`, for
  example `android/native-current.apk`. Packages land at
  `<root>/packages/<recipe-id>/<version>/` with `<recipe-id>/LATEST.txt` describing the newest.
* **Google Play.** Uploads the AAB to the configured track (default `internal`) with the
  Google Play Developer API (`pip install -e '.[play]'`) or `fastlane supply`.
* **TestFlight.** Uploads the IPA with an App Store Connect API key, through `xcrun altool`
  (default) or `fastlane pilot`.

When Play or TestFlight credentials are missing, those uploads are **skipped with a message**
and the build and shared-folder copy still run.

**Store collision rule.** Current and legacy demos often share an applicationId or bundle id.
Only recipes with `store.play: true` or `store.testflight: true` are uploaded, and within one
run each id is uploaded once. The shipped recipes enable stores for `*-current` and `flutter`
and disable them for `*-legacy`.

## Recipes

A recipe has `demos` and `packages`, each a list of steps:

```yaml
recipes:
  android-current:
    platform: android
    version: {file: gradle.properties, regex: '^\s*VERSION_NAME\s*=\s*(\S+)'}
    store: {play: true, android_package: "${custom.app_ids.android_native:-com.example.demo}"}
    demos:
      - type: gradle
        tasks: [":app:assembleRelease", ":app:bundleRelease"]
        collect:
          - {glob: "app/build/outputs/apk/release/*.apk", kind: apk, target: android-native-current}
          - {glob: "app/build/outputs/bundle/release/*.aab", kind: aab}
```

| Step type | Runs |
| --- | --- |
| `run` | Any command (`cmd` as a list or a string, no shell) |
| `gradle` | `./gradlew <tasks>` with JAVA_HOME, Android SDK and optional release signing |
| `flutter` | `flutter <args>` |
| `xcode-simulator` | `xcodebuild` for the iOS Simulator; collects the `.app` |
| `xcode-ipa` | Archive and export an IPA (App Store Connect key used for automatic signing when set) |
| `xcframework` | Device and simulator archives combined into a zipped XCFramework |
| `npm-pack` | `npm pack` |
| `source-zip` | `git archive` of the committed sources (or a filtered file walk) |

Every step accepts `workdir` (relative to the checkout), `env`, `when: [play|testflight|onedrive]`
(run only when that destination is active, for example IPA archives only for TestFlight),
`host: macos` and `collect`.

Strings may use `${custom.dotted.key}`, `${env.NAME}`, `${version}` and `${recipe}`, with an
optional default: `${custom.ios_team_id:-}`.

The version comes from the recipe's `version` (a file plus regex, or a command), then
`git describe`, then a timestamp.

## Security model

* Secrets are never stored in the repository or in plain YAML. `keys.yaml` holds file paths
  and references such as `{source: keychain, name: android.store_password}` or
  `{source: env, name: ASC_KEY_ID}`. Values are read at run time.
* Settings files are written with mode `600` and the settings directory with mode `700`. Key
  files uploaded through the UI are stored in `<settings>/keys/` with mode `600`.
* Android signing is passed to Gradle through `ORG_GRADLE_PROJECT_*` environment variables, not
  the command line, so passwords do not show up in process listings.
* Every log line passes through a redactor that masks known secret values. The Keys page and
  `status` command only report "configured" or "missing".
* The Settings UI binds to `127.0.0.1`, accepts only loopback `Host` headers, and requires a
  per-process token on every API call, so other websites in your browser cannot drive it.
* `.gitignore` excludes settings files, staging output and key material.

## Output

Each real run writes `<staging>/runs/<timestamp>/` with the collected artifacts per recipe,
`log.txt` and `report.json` (selections, versions, commits, artifacts, upload results).
Intermediate build products (derived data, archives) are kept in `<staging>/work/` between
runs to speed up rebuilds.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). AI coding agents should read [AGENTS.md](AGENTS.md)
first.

## License

MIT. See [LICENSE](LICENSE).
