"""Command line interface. Mirrors the Run page of the Settings UI."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import sys

import typer

from . import __version__
from .models import DESTINATIONS
from .paths import data_dir, key_files_dir, settings_file, user_home
from .recipes import RecipeError, load_recipes, recipe_sources
from .runlog import RunLog

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Build mobile SDK demo apps and packages, then publish them.",
)
keys_app = typer.Typer(no_args_is_help=True, help="Manage secrets in the OS keychain.")
app.add_typer(keys_app, name="keys")


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items


@app.command()
def version() -> None:
    """Print the tool version."""
    typer.echo(__version__)


@app.command()
def init(force: bool = typer.Option(False, help="Overwrite existing settings files.")) -> None:
    """Create user settings from the shipped examples."""
    home = user_home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    key_files_dir().mkdir(exist_ok=True)
    os.chmod(key_files_dir(), 0o700)
    examples = data_dir("examples")
    for kind in ("repos", "outputs", "keys", "custom"):
        target = settings_file(kind)
        source = examples / f"{kind}.example.yaml"
        if target.exists() and not force:
            typer.echo(f"keep     {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
        typer.echo(f"created  {target}")
    typer.echo("\nNext: run 'sdk-release-kit serve' and fill in Repositories, Outputs and Keys.")


@app.command()
def serve(
    port: int = typer.Option(8765, help="Port on 127.0.0.1."),
    host: str = typer.Option("127.0.0.1", help="Bind address (loopback only by default)."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
) -> None:
    """Open the Settings UI."""
    from .server import serve as run_server

    run_server(host=host, port=port, open_browser=not no_browser)


@app.command()
def recipes() -> None:
    """List recipe ids and where recipes were loaded from."""
    try:
        loaded = load_recipes()
    except RecipeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    for src in recipe_sources():
        typer.echo(f"# {src}")
    for rid, r in loaded.recipes.items():
        stores = [s for s in ("play", "testflight") if getattr(r.store, s)]
        typer.echo(
            f"{rid:22} {r.platform:12} demos:{len(r.demos)} packages:{len(r.packages)}"
            f"  stores:{','.join(stores) or '-'}  {r.label}"
        )


@app.command()
def status() -> None:
    """Show settings files, key status (never values) and tool availability."""
    from .store import keys as keys_store
    from .store import load_all

    typer.echo(f"settings directory: {user_home()}")
    for kind in ("repos", "outputs", "keys", "custom"):
        f = settings_file(kind)
        typer.echo(f"  {kind:8} {'ok     ' if f.exists() else 'missing'} {f}")
    settings = load_all()
    typer.echo("repositories:")
    for rid, entry in settings.repos.repos.items():
        from .paths import expand

        p = expand(entry.path)
        state = "disabled" if not entry.enabled else ("ok" if p and p.is_dir() else "missing")
        typer.echo(f"  {rid:22} {state:9} {entry.path}")
    typer.echo("keys:")
    for name, st in keys_store.status(settings.keys).items():
        typer.echo(
            f"  {name:24} {'configured' if st['configured'] else 'missing   '} {st['detail']}"
        )
    typer.echo("tools:")
    for tool in ("git", "java", "flutter", "xcodebuild", "pod", "npm", "fastlane"):
        typer.echo(f"  {tool:12} {shutil.which(tool) or 'not found'}")


@app.command()
def build(
    sdks: str = typer.Option(None, help="Comma-separated recipe ids. Default: all enabled repos."),
    artifacts: str = typer.Option("both", help="demos, packages or both."),
    dest: str = typer.Option(
        None,
        help=f"Comma-separated destinations ({', '.join(DESTINATIONS)}). Default: enabled ones.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan only."),
    skip_upload: bool = typer.Option(False, "--skip-upload", help="Build only, publish nothing."),
    json_out: bool = typer.Option(False, "--json", help="Print the report as JSON at the end."),
) -> None:
    """Build the selected SDKs and publish to the chosen destinations."""
    from .pipeline import Pipeline, RunOptions

    if artifacts not in ("demos", "packages", "both"):
        raise typer.BadParameter("must be demos, packages or both", param_hint="--artifacts")
    log = RunLog()
    log.subscribe(lambda line: print(line, flush=True))
    opts = RunOptions(
        sdks=_split(sdks),
        artifacts=artifacts,
        destinations=_split(dest),
        dry_run=dry_run,
        skip_upload=skip_upload,
    )
    try:
        report = Pipeline(log=log).run(opts)
    except (RecipeError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    if json_out:
        typer.echo(json.dumps(report, indent=2))
    if not dry_run and not report.get("ok"):
        raise typer.Exit(1)


@keys_app.command("set")
def keys_set(
    name: str = typer.Argument(..., help="Keychain entry name, e.g. android.store_password"),
) -> None:
    """Store a secret in the OS keychain (prompted, never echoed)."""
    from .store.keys import set_keychain_secret

    value = getpass.getpass(f"value for {name}: ")
    if not value:
        typer.echo("empty value, nothing stored", err=True)
        raise typer.Exit(1)
    set_keychain_secret(name, value)
    typer.echo(f"stored keychain entry '{name}' (service sdk-release-kit)")


@keys_app.command("delete")
def keys_delete(name: str) -> None:
    """Remove a secret from the OS keychain."""
    from .store.keys import delete_keychain_secret

    delete_keychain_secret(name)
    typer.echo(f"deleted keychain entry '{name}'")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
