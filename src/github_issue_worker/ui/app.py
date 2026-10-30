"""The UI's routes (F14.1 to F14.9). Pages read a `Backend`; every change goes through it."""

from __future__ import annotations

import hashlib
import secrets
from typing import Annotated, Any
from urllib.parse import parse_qsl

from fastapi import FastAPI, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)

from github_issue_worker.application.history import SplitBy
from github_issue_worker.application.repository_suggestions import suggest
from github_issue_worker.cli import normalize_repository
from github_issue_worker.configuration import (
    Configuration,
    ConfigurationError,
    RepositorySettings,
    WorkerSettings,
    validate,
)
from github_issue_worker.ui import pages
from github_issue_worker.ui.forms import FormField, apply_form, fields_of
from github_issue_worker.ui.model import ActionError, Backend

COOKIE = "ghiw_token"


def _settings_fields() -> list[Any]:
    return fields_of(Configuration, skip=frozenset({"workers", "repositories", "accounts"}))


def _problems(error: ConfigurationError) -> tuple[list[str], frozenset[str]]:
    return (
        [f"{problem.path}: {problem.message}" for problem in error.problems],
        frozenset(problem.path for problem in error.problems),
    )


def _dump(config: Configuration) -> dict[str, Any]:
    return config.model_dump(mode="json", by_alias=True, exclude_unset=True)


def create_app(backend: Backend) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def require_token(request: Request, call_next: Any) -> Any:
        token = backend.config().ui.token
        if token:
            given = request.query_params.get("token") or request.cookies.get(COOKIE) or ""
            if not secrets.compare_digest(given, token):
                return PlainTextResponse("A token is required: open this page with ?token=...", 401)
            response = await call_next(request)
            if request.query_params.get("token"):
                response.set_cookie(COOKIE, token, httponly=True, samesite="strict")
            return response
        return await call_next(request)

    async def form(request: Request) -> dict[str, str]:
        raw = (await request.body()).decode("utf-8")
        return dict(parse_qsl(raw, keep_blank_values=True))

    def back(path: str, notice: str = "") -> RedirectResponse:
        target = f"{path}?notice={notice}" if notice else path
        return RedirectResponse(target, status_code=303)

    def save(data: dict[str, Any]) -> Configuration:
        config = validate(data)
        backend.save_config(config)
        return config

    # --- pages -----------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def issues(fragment: int = 0, notice: str = "") -> str:
        snapshot = backend.snapshot()
        if fragment:
            return pages.issues_fragment(snapshot)
        return pages.issues_page(snapshot, notice)

    @app.get("/workers", response_class=HTMLResponse)
    def workers(
        fragment: int = 0,
        notice: str = "",
        by: SplitBy = "model",
        days: Annotated[int, Query(ge=7, le=366)] = 30,
        time: SplitBy = "model",
    ) -> str:
        snapshot = backend.snapshot()
        view = pages.CockpitView(by=by, days=days, time=time)
        colors = backend.config().model_colors
        if fragment:
            return pages.cockpit_fragment(snapshot, backend.history(), view, colors)
        return pages.cockpit_page(snapshot, backend.history(), view, notice, colors)

    @app.get("/repositories", response_class=HTMLResponse)
    def repositories(notice: str = "") -> str:
        return pages.repositories_page(backend.snapshot(), backend.config(), notice)

    @app.get("/fleet", response_class=HTMLResponse)
    def fleet(fragment: int = 0, notice: str = "") -> str:
        snapshot = backend.snapshot()
        return pages.fleet_fragment(snapshot) if fragment else pages.fleet_page(snapshot, notice)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(notice: str = "") -> str:
        return pages.settings_page(backend.snapshot().manager_id, backend.config(), notice)

    @app.get("/events", response_class=PlainTextResponse)
    def events() -> str:
        """A digest of what the live pages show; it changes when they should refresh.

        A short request the page polls, not a stream: a stream per open tab used up the
        browser's few connections per host, so a click on any button waited forever. A plain
        ``def`` runs in the thread pool, so a slow snapshot never stalls the server's loop.
        """
        snapshot = backend.snapshot()
        return hashlib.sha1(
            repr((snapshot.workers, snapshot.claims, snapshot.repositories)).encode()
        ).hexdigest()

    # --- settings --------------------------------------------------------------------------------

    @app.post("/settings", response_class=HTMLResponse, response_model=None)
    async def save_settings(request: Request) -> Any:
        submitted = await form(request)
        config = backend.config()
        data = apply_form(_dump(config), _settings_fields(), submitted, Configuration(), config)
        accounts: dict[str, Any] = {}
        index = 0
        while f"accounts[{index}].name" in submitted:
            name = submitted[f"accounts[{index}].name"].strip()
            runs = submitted.get(f"accounts[{index}].maxConcurrentRuns", "").strip()
            if name and submitted.get(f"accounts[{index}].remove") != "true":
                accounts[name] = {"maxConcurrentRuns": runs} if runs else {}
            index += 1
        if accounts or config.accounts:
            data["accounts"] = accounts
        try:
            save(data)
        except ConfigurationError as error:
            problems, paths = _problems(error)
            return HTMLResponse(
                pages.settings_page(
                    backend.snapshot().manager_id,
                    config,
                    problems=problems,
                    error_paths=paths,
                    submitted=submitted,
                ),
                status_code=422,
            )
        return back("/settings", "Settings saved and applied.")

    # --- repositories ----------------------------------------------------------------------------

    @app.get("/repositories/suggest")
    def suggest_repositories(q: str = "") -> JSONResponse:
        watched = [r.repository for r in backend.config().repositories]
        found = suggest(backend.pushable_repositories(), watched, q.strip())
        return JSONResponse({"kind": found.kind, "items": list(found.items)})

    @app.post("/repositories/add", response_class=HTMLResponse, response_model=None)
    async def add_repository(request: Request) -> Any:
        submitted = await form(request)
        config = backend.config()
        try:
            name = normalize_repository(submitted.get("repository", ""))
            backend.check_repository(name)
        except (ValueError, ActionError) as error:
            return HTMLResponse(
                pages.repositories_page(backend.snapshot(), config, problems=[str(error)]), 422
            )
        if name not in {r.repository for r in config.repositories}:
            data = _dump(config)
            data["repositories"] = [*data.get("repositories", []), {"repository": name}]
            save(data)
        return back("/repositories", f"Watching {name}.")

    @app.post("/repositories/remove")
    async def remove_repository(request: Request) -> RedirectResponse:
        name = (await form(request)).get("repository", "")
        data = _dump(backend.config())
        data["repositories"] = [r for r in data.get("repositories", []) if r["repository"] != name]
        save(data)
        return back("/repositories", f"No longer watching {name}.")

    @app.post("/repositories/save", response_class=HTMLResponse, response_model=None)
    async def save_repository(request: Request) -> Any:
        submitted = await form(request)
        name = submitted.get("repository", "")
        config = backend.config()
        data = _dump(config)
        repositories = data.get("repositories", [])
        position = next(i for i, r in enumerate(repositories) if r["repository"] == name)
        current = next(r for r in config.repositories if r.repository == name)
        fields = [f for f in fields_of(RepositorySettings) if f.path != "repository"]
        repositories[position] = {
            "repository": name,
            **{
                key: value
                for key, value in apply_form(
                    {k: v for k, v in repositories[position].items() if k != "repository"},
                    fields,
                    submitted,
                    RepositorySettings(repository=name),
                    current,
                ).items()
            },
        }
        try:
            save(data)
        except ConfigurationError as error:
            problems, _ = _problems(error)
            return HTMLResponse(
                pages.repositories_page(backend.snapshot(), config, problems=problems), 422
            )
        return back("/repositories", f"Overrides of {name} saved.")

    # --- workers ---------------------------------------------------------------------------------

    def worker_data(submitted: dict[str, str]) -> dict[str, Any]:
        data: dict[str, Any] = apply_form(
            {}, pages.WORKER_FIELDS, submitted, _blank_worker(), _blank_worker()
        )
        data["name"] = submitted.get("name", "").strip()
        data["agent"] = submitted.get("agent", "")
        data["account"] = submitted.get("account", "")
        kind = submitted.get("installation.type", "path")
        installation: dict[str, Any] = {"type": kind}
        if kind == "executable":
            installation["path"] = submitted.get("installation.path", "")
            env = apply_form(
                {},
                [FormField("env", "", "env")],
                {"env": submitted.get("installation.env", "")},
                _blank_worker(),
                _blank_worker(),
            )
            installation["env"] = env.get("env", {})
        if (
            kind in ("claudecodeportable", "claudelocal")
            and submitted.get("installation.root", "").strip()
        ):
            installation["root"] = submitted["installation.root"].strip()
        if kind == "claudecodeportable" and submitted.get("installation.profile", "").strip():
            installation["profile"] = submitted["installation.profile"].strip()
        data["installation"] = installation
        return data

    @app.get("/workers/new", response_class=HTMLResponse)
    def new_worker() -> str:
        config = backend.config()
        return pages.worker_page(
            backend.snapshot().manager_id, config, None, backend.detect_workers()
        )

    @app.post("/workers/add-detected")
    async def add_detected(request: Request) -> RedirectResponse:
        index = int((await form(request)).get("index", "-1"))
        detected = backend.detect_workers()
        if not 0 <= index < len(detected):
            return back("/workers/new")
        found = dict(detected[index])
        data = _dump(backend.config())
        names = {w["name"] for w in data.get("workers", [])}
        base, suffix = str(found["name"]), 2
        while found["name"] in names:
            found["name"] = f"{base}-{suffix}"
            suffix += 1
        accounts = data.setdefault("accounts", {})
        if found["account"] not in accounts:
            local = found["installation"]["type"] == "claudelocal"  # type: ignore[index]
            accounts[found["account"]] = {"maxConcurrentRuns": 1} if local else {}
        data["workers"] = [*data.get("workers", []), found]
        save(data)
        return back("/", f"Worker {found['name']} added.")

    @app.post("/workers/new", response_class=HTMLResponse, response_model=None)
    async def create_worker(request: Request) -> Any:
        submitted = await form(request)
        data = _dump(backend.config())
        data["workers"] = [*data.get("workers", []), worker_data(submitted)]
        try:
            save(data)
        except ConfigurationError as error:
            problems, _ = _problems(error)
            return HTMLResponse(
                pages.worker_page(
                    backend.snapshot().manager_id,
                    backend.config(),
                    None,
                    backend.detect_workers(),
                    problems,
                ),
                422,
            )
        return back("/", f"Worker {submitted.get('name', '')} added.")

    @app.get("/workers/{name}", response_class=HTMLResponse)
    def edit_worker(name: str) -> Any:
        config = backend.config()
        worker = next((w for w in config.workers if w.name == name), None)
        if worker is None:
            return back("/")
        return pages.worker_page(backend.snapshot().manager_id, config, worker)

    @app.post("/workers/{name}", response_class=HTMLResponse, response_model=None)
    async def update_worker(name: str, request: Request) -> Any:
        submitted = await form(request)
        config = backend.config()
        data = _dump(config)
        data["workers"] = [
            worker_data(submitted) if w["name"] == name else w for w in data.get("workers", [])
        ]
        try:
            save(data)
        except ConfigurationError as error:
            problems, _ = _problems(error)
            worker = next(w for w in config.workers if w.name == name)
            return HTMLResponse(
                pages.worker_page(backend.snapshot().manager_id, config, worker, None, problems),
                422,
            )
        return back("/", f"Worker {submitted.get('name', name)} saved.")

    @app.post("/workers/{name}/remove")
    def remove_worker(name: str) -> RedirectResponse:
        data = _dump(backend.config())
        data["workers"] = [w for w in data.get("workers", []) if w["name"] != name]
        save(data)
        return back("/", f"Worker {name} removed; its issue goes back to the queue.")

    def came_from(request: Request) -> str:
        """Back to the page the action was taken on: Issues, Workers or a log."""
        referer = request.headers.get("referer", "")
        path = "/" + referer.split("://", 1)[-1].split("/", 1)[-1] if "://" in referer else ""
        path = path.split("?", 1)[0]
        return path if path in ("/", "/workers") or path.endswith("/log") else "/workers"

    @app.post("/workers/{name}/pause")
    def pause_worker(name: str, request: Request) -> RedirectResponse:
        backend.set_worker_enabled(name, False)
        return back(came_from(request), f"{name} takes no new issue until resumed.")

    @app.post("/workers/{name}/resume")
    def resume_worker(name: str, request: Request) -> RedirectResponse:
        backend.set_worker_enabled(name, True)
        return back(came_from(request), f"{name} takes issues again.")

    @app.post("/workers/{name}/approve-ci")
    def approve_ci(name: str, request: Request) -> RedirectResponse:
        try:
            backend.approve_ci(name)
        except ActionError as error:
            return back(came_from(request), str(error))
        return back(came_from(request), f"CI of {name}'s pull request was approved and starts now.")

    @app.post("/workers/{name}/stop", response_class=HTMLResponse, response_model=None)
    def stop_worker(name: str, request: Request) -> Any:
        try:
            backend.stop_worker(name)
        except ActionError as error:
            return HTMLResponse(pages.issues_page(backend.snapshot(), problems=[str(error)]), 422)
        return back(
            came_from(request),
            f"{name} was stopped and is paused; its issue is free for other workers.",
        )

    @app.post("/workers/{name}/open", response_class=HTMLResponse, response_model=None)
    def open_worktree(name: str, request: Request) -> Any:
        try:
            backend.open_worktree(name)
        except ActionError as error:
            return back(came_from(request), str(error))
        return back(came_from(request), f"The worktree of {name} is open in the file manager.")

    @app.get("/workers/{name}/log", response_class=HTMLResponse, response_model=None)
    def worker_log(name: str, fragment: int = 0) -> Any:
        snapshot = backend.snapshot()
        worker = next((w for w in snapshot.workers if w.name == name), None)
        if worker is None:
            return back("/workers")
        text = backend.log(name)
        return pages.log_fragment(text) if fragment else pages.log_page(snapshot, worker, text)

    # --- claims ----------------------------------------------------------------------------------

    @app.post("/claims/release", response_class=HTMLResponse, response_model=None)
    async def release(request: Request) -> Any:
        submitted = await form(request)
        repository = submitted.get("repository", "")
        issue = int(submitted.get("issue", "0"))
        confirm = submitted.get("confirm")
        force = confirm is not None
        if confirm is not None and confirm.strip() != f"{repository}#{issue}":
            return HTMLResponse(
                pages.fleet_page(
                    backend.snapshot(),
                    problems=[
                        f"Type {repository}#{issue} exactly to release another manager's claim."
                    ],
                ),
                422,
            )
        try:
            backend.release(repository, issue, force)
        except ActionError as error:
            return HTMLResponse(pages.fleet_page(backend.snapshot(), problems=[str(error)]), 422)
        return back("/fleet", f"Released {repository}#{issue}.")

    return app


def _blank_worker() -> WorkerSettings:
    return WorkerSettings(name="x", agent="claude", account="x")
