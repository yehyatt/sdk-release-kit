"""Settings UI and JSON API. Binds to localhost only.

Every ``/api`` request must carry the ``X-SRK-Token`` header. The token is generated per
server process and embedded in the served page, so another website open in the same browser
cannot change settings or start builds (it can neither read the page nor send custom headers
cross-origin). Host headers are restricted to loopback names to block DNS rebinding.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import __version__
from .models import (
    DESTINATIONS,
    SCHEMA_VERSION,
    KeysSettings,
    OutputsSettings,
    ReposSettings,
    SecretRef,
)
from .paths import data_dir, expand, settings_file, user_home
from .pipeline import Pipeline, RunOptions
from .recipes import RecipeError, load_recipes, recipe_sources
from .runlog import RunLog
from .store import custom as custom_store
from .store import keys as keys_store
from .store import outputs as outputs_store
from .store import repos as repos_store

TOKEN = secrets.token_urlsafe(32)


def require_token(x_srk_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_srk_token, TOKEN):
        raise HTTPException(status_code=403, detail="missing or invalid X-SRK-Token")


class Job:
    def __init__(self, opts: RunOptions) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.opts = opts
        self.log = RunLog()
        self.done = False
        self.result: dict[str, Any] | None = None
        self.error: str | None = None


JOBS: dict[str, Job] = {}
_RUN_LOCK = threading.Lock()


def _run_job(job: Job) -> None:
    try:
        with _RUN_LOCK:
            job.result = Pipeline(log=job.log).run(job.opts)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        job.error = job.log.redact(str(exc))
        job.log.info(f"ERROR: {job.error}")
    finally:
        job.done = True


class RawText(BaseModel):
    text: str


class PathQuery(BaseModel):
    path: str


class SecretValue(BaseModel):
    field: str  # e.g. "android.store_password"
    value: str


class KeyUpload(BaseModel):
    field: str  # android.keystore_path | play.json_key_path | testflight.key_path
    filename: str
    content_b64: str


class RunRequest(BaseModel):
    sdks: list[str] = []
    artifacts: str = "both"
    destinations: list[str] = []
    dry_run: bool = False
    skip_upload: bool = False


_SECRET_FIELDS = {
    "android.store_password": ("android", "store_password"),
    "android.key_password": ("android", "key_password"),
    "testflight.key_id": ("testflight", "key_id"),
    "testflight.issuer_id": ("testflight", "issuer_id"),
}
_FILE_FIELDS = {
    "android.keystore_path": ("android", "keystore_path"),
    "play.json_key_path": ("play", "json_key_path"),
    "testflight.key_path": ("testflight", "key_path"),
}


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def create_app() -> FastAPI:
    app = FastAPI(title="SDK Release Kit", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    web = data_dir("web")
    app.mount("/static", StaticFiles(directory=web), name="static")
    api = [Depends(require_token)]

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = (web / "index.html").read_text(encoding="utf-8")
        return html.replace("{{SRK_TOKEN}}", TOKEN)

    @app.get("/api/status", dependencies=api)
    def status() -> dict[str, Any]:
        try:
            recipes = load_recipes()
            recipe_info = {
                rid: {"platform": r.platform, "label": r.label, "store": r.store.model_dump()}
                for rid, r in recipes.recipes.items()
            }
            recipe_error = None
        except RecipeError as exc:
            recipe_info, recipe_error = {}, str(exc)
        return {
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "user_home": str(user_home()),
            "files": {k: str(settings_file(k)) for k in ("repos", "outputs", "keys", "custom")},
            "recipe_sources": [str(p) for p in recipe_sources()],
            "recipes": recipe_info,
            "recipe_error": recipe_error,
            "destinations": list(DESTINATIONS),
        }

    # Repos / Outputs ---------------------------------------------------------------------------
    @app.get("/api/settings/repos", dependencies=api)
    def get_repos() -> dict[str, Any]:
        return repos_store.load().model_dump(mode="json")

    @app.put("/api/settings/repos", dependencies=api)
    def put_repos(body: dict[str, Any]) -> dict[str, Any]:
        try:
            current = repos_store.load().model_dump(mode="json")
            settings = ReposSettings.model_validate({**current, **body})
        except ValidationError as exc:
            raise _bad_request(exc) from exc
        repos_store.save(settings)
        return settings.model_dump(mode="json")

    @app.get("/api/settings/outputs", dependencies=api)
    def get_outputs() -> dict[str, Any]:
        return outputs_store.load().model_dump(mode="json")

    @app.put("/api/settings/outputs", dependencies=api)
    def put_outputs(body: dict[str, Any]) -> dict[str, Any]:
        try:
            current = outputs_store.load().model_dump(mode="json")
            settings = OutputsSettings.model_validate({**current, **body})
        except ValidationError as exc:
            raise _bad_request(exc) from exc
        outputs_store.save(settings)
        return settings.model_dump(mode="json")

    # Keys ---------------------------------------------------------------------------------------
    @app.get("/api/settings/keys", dependencies=api)
    def get_keys() -> dict[str, Any]:
        settings = keys_store.load()
        return {"settings": settings.model_dump(mode="json"), "status": keys_store.status(settings)}

    @app.put("/api/settings/keys", dependencies=api)
    def put_keys(body: dict[str, Any]) -> dict[str, Any]:
        try:
            current = keys_store.load().model_dump(mode="json")
            merged = {
                **current,
                **{
                    k: {**current.get(k, {}), **v} if isinstance(v, dict) else v
                    for k, v in body.items()
                },
            }
            settings = KeysSettings.model_validate(merged)
        except ValidationError as exc:
            raise _bad_request(exc) from exc
        keys_store.save(settings)
        return {"settings": settings.model_dump(mode="json"), "status": keys_store.status(settings)}

    @app.post("/api/keys/secret", dependencies=api)
    def set_secret(body: SecretValue) -> dict[str, Any]:
        if body.field not in _SECRET_FIELDS:
            raise HTTPException(400, f"unknown secret field {body.field}")
        group, name = _SECRET_FIELDS[body.field]
        settings = keys_store.load()
        ref: SecretRef = getattr(getattr(settings, group), name)
        if ref.source != "keychain" or not ref.name:
            ref = SecretRef(source="keychain", name=body.field)
            setattr(getattr(settings, group), name, ref)
        try:
            if body.value:
                keys_store.set_keychain_secret(ref.name, body.value)
            else:
                keys_store.delete_keychain_secret(ref.name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"keychain error: {type(exc).__name__}") from exc
        keys_store.save(settings)
        return {"settings": settings.model_dump(mode="json"), "status": keys_store.status(settings)}

    @app.post("/api/keys/upload", dependencies=api)
    def upload_key(body: KeyUpload) -> dict[str, Any]:
        if body.field not in _FILE_FIELDS:
            raise HTTPException(400, f"unknown key file field {body.field}")
        try:
            path = keys_store.store_key_file(body.filename, body.content_b64)
        except ValueError as exc:
            raise _bad_request(exc) from exc
        group, name = _FILE_FIELDS[body.field]
        settings = keys_store.load()
        setattr(getattr(settings, group), name, str(path))
        keys_store.save(settings)
        return {"settings": settings.model_dump(mode="json"), "status": keys_store.status(settings)}

    # Custom -------------------------------------------------------------------------------------
    @app.get("/api/settings/custom", dependencies=api)
    def get_custom() -> dict[str, Any]:
        return {"text": custom_store.load_raw()}

    @app.put("/api/settings/custom", dependencies=api)
    def put_custom(body: RawText) -> dict[str, Any]:
        try:
            data = custom_store.save_raw(body.text)
        except Exception as exc:  # noqa: BLE001 - YAML errors
            raise _bad_request(exc) from exc
        return {"text": custom_store.load_raw(), "keys": list(data)}

    # Helpers ------------------------------------------------------------------------------------
    @app.post("/api/validate/path", dependencies=api)
    def validate_path(body: PathQuery) -> dict[str, Any]:
        p = expand(body.path)
        if p is None:
            return {"ok": False, "detail": "empty"}
        if p.is_dir():
            git = (p / ".git").exists()
            return {
                "ok": True,
                "detail": "folder found" + (" (git checkout)" if git else ""),
                "resolved": str(p),
            }
        if p.is_file():
            return {"ok": True, "detail": "file found", "resolved": str(p)}
        return {"ok": False, "detail": "not found", "resolved": str(p)}

    # Runs ---------------------------------------------------------------------------------------
    @app.post("/api/runs", dependencies=api)
    def start_run(body: RunRequest) -> dict[str, Any]:
        if body.artifacts not in ("demos", "packages", "both"):
            raise HTTPException(400, "artifacts must be demos, packages or both")
        if any(not j.done for j in JOBS.values()):
            raise HTTPException(409, "a run is already in progress")
        opts = RunOptions(
            sdks=body.sdks or None,
            artifacts=body.artifacts,
            destinations=body.destinations,
            dry_run=body.dry_run,
            skip_upload=body.skip_upload,
        )
        job = Job(opts)
        JOBS[job.id] = job
        threading.Thread(target=_run_job, args=(job,), daemon=True).start()
        return {"id": job.id}

    @app.get("/api/runs/{job_id}", dependencies=api)
    def run_status(job_id: str) -> dict[str, Any]:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown run")
        return {"id": job.id, "done": job.done, "error": job.error, "result": job.result}

    @app.get("/api/runs/{job_id}/log", dependencies=api)
    async def run_log(job_id: str, request: Request) -> StreamingResponse:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown run")

        async def stream():
            sent = 0
            while True:
                lines = job.log.lines
                for line in lines[sent:]:
                    yield line + "\n"
                sent = len(lines)
                if job.done and sent == len(job.log.lines):
                    break
                if await request.is_disconnected():
                    break
                await asyncio.sleep(0.3)

        return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("the Settings UI only binds to a loopback address")
    url = f"http://127.0.0.1:{port}/"
    print(f"SDK Release Kit Settings UI: {url}")
    print(f"settings directory: {user_home()}")
    if open_browser:
        threading.Timer(1.0, lambda: __import__("webbrowser").open(url)).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


__all__ = ["create_app", "serve"]
