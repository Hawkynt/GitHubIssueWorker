"""The UI's pages as server-rendered HTML: no build step, no external assets, works offline."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from html import escape
from typing import Any

from pydantic import BaseModel

from github_issue_worker.adapters.transcript import Entry, transcript
from github_issue_worker.configuration import (
    AccountSettings,
    Configuration,
    DefaultsSettings,
    RepositorySettings,
    WorkerSettings,
)
from github_issue_worker.ui.forms import HELP, FormField, display, fields_of, is_set, value_at
from github_issue_worker.ui.model import STEPS, ClaimView, IssueView, Snapshot, WorkerView

STYLE = """
:root {
  --paper: #edf0f3; --sheet: #f7f9fb; --ink: #1b2430; --muted: #5a6775; --rule: #c8d0d9;
  --steel: #2e5c8a; --steel-soft: #d6e2ee; --amber: #b7791f; --amber-soft: #f3e6cf;
  --brick: #a63d2f; --brick-soft: #f2dcd8; --done: #4d6b52;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #141a21; --sheet: #1b232c; --ink: #e3e8ee; --muted: #9aa6b3; --rule: #33404d;
    --steel: #7fa8d1; --steel-soft: #23384d; --amber: #e0a44a; --amber-soft: #3a2f1c;
    --brick: #e07a6b; --brick-soft: #3b2420; --done: #8fb596; color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--paper); color: var(--ink);
  font: 15px/1.5 system-ui, "Segoe UI", "Noto Sans", "Helvetica Neue", sans-serif; }
a { color: var(--steel); }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible,
textarea:focus-visible, summary:focus-visible { outline: 2px solid var(--steel); outline-offset: 2px; }
code, .mono { font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace; font-size: 13px; }
.frame { display: grid; grid-template-columns: 13rem 1fr; min-height: 100vh; }
nav { border-right: 1px solid var(--rule); padding: 1.5rem 1rem; }
nav .who { font-weight: 650; margin-bottom: .25rem; }
nav .id { color: var(--muted); font-size: 13px; margin-bottom: 1.75rem; overflow-wrap: anywhere; }
nav a { display: block; padding: .4rem .6rem; border-radius: 4px; color: var(--ink);
  text-decoration: none; }
nav a[aria-current="page"] { background: var(--steel-soft); color: var(--steel); font-weight: 600; }
main { padding: 1.75rem 2.25rem 3rem; max-width: 70rem; }
h1 { font-size: 1.6rem; font-weight: 650; margin: 0 0 .25rem; letter-spacing: -.01em; }
.lede { color: var(--muted); margin: 0 0 1.75rem; max-width: 42rem; }
h2 { font-size: 1.1rem; margin: 2rem 0 .5rem; }
.lane { display: grid; grid-template-columns: 14rem 1fr auto; gap: 1.25rem; align-items: start;
  padding: 1.1rem 0; border-top: 1px solid var(--rule); }
.lane:last-child { border-bottom: 1px solid var(--rule); }
.lane .name { font-weight: 650; font-size: 1.05rem; }
.lane .runs { color: var(--muted); font-size: 13.5px; }
.lane .model { display: inline-block; margin-top: .3rem; font-weight: 600; }
.issue { font-weight: 600; }
.issue .repo { color: var(--muted); font-weight: 400; }
.track { display: grid; grid-template-columns: repeat(6, 1fr); gap: 3px; margin: .65rem 0 .35rem;
  max-width: 36rem; }
.track span { font-size: 12px; color: var(--muted); padding-top: .4rem;
  border-top: 5px solid var(--rule); }
.track span.past { border-top-color: var(--steel); color: var(--ink); }
.track span.now { border-top-color: var(--steel); color: var(--steel); font-weight: 650; }
.facts { color: var(--muted); font-size: 13.5px; }
.facts .mono { color: var(--ink); }
.quiet { color: var(--muted); }
.flag { display: inline-block; padding: .1rem .5rem; border-radius: 3px; font-size: 13px;
  font-weight: 600; }
.flag.limited, .flag.paused { background: var(--amber-soft); color: var(--amber); }
.flag.unavailable, .flag.failed { background: var(--brick-soft); color: var(--brick); }
.flag.working { background: var(--steel-soft); color: var(--steel); }
.actions { display: flex; gap: .4rem; flex-wrap: wrap; justify-content: flex-end; }
.nowrap { white-space: nowrap; }
.where { display: block; font-size: 13px; color: var(--muted); }
.where.local { color: var(--steel); }
td .actions { justify-content: flex-start; }
.log { list-style: none; margin: 0; padding: 0; max-width: 56rem; }
.log li { padding: .55rem 0; border-top: 1px solid var(--rule); }
.log .tool { font-family: ui-monospace, "Cascadia Code", "Source Code Pro", monospace; font-size: 13px; }
.log .tool strong { font-family: system-ui, sans-serif; font-size: 13px; color: var(--steel); margin-right: .5rem; }
.log pre { margin: .25rem 0 0; padding: .5rem .75rem; background: var(--sheet); border-radius: 4px;
  white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12.5px; max-height: 18rem; overflow: auto; }
.log .thinking { color: var(--muted); }
.log .error { color: var(--brick); }
.log .done { font-weight: 600; }
.holder { white-space: nowrap; }
.holder .quiet { display: block; font-size: 13px; }
a.button { display: inline-block; text-decoration: none; }
button, .button { font: inherit; font-size: 13.5px; padding: .35rem .8rem; border-radius: 4px;
  border: 1px solid var(--rule); background: var(--sheet); color: var(--ink); cursor: pointer; }
button.primary { background: var(--steel); border-color: var(--steel); color: #fff; }
button.danger { color: var(--brick); }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th { text-align: left; font-weight: 600; color: var(--muted); padding: .45rem .75rem .45rem 0;
  border-bottom: 1px solid var(--rule); }
td { padding: .55rem .75rem .55rem 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
form.inline { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; margin: 1rem 0; }
input, select, textarea { font: inherit; font-size: 14px; padding: .35rem .5rem; color: var(--ink);
  background: var(--sheet); border: 1px solid var(--rule); border-radius: 4px; }
fieldset { border: 0; border-top: 1px solid var(--rule); margin: 1.5rem 0 0; padding: 1rem 0 0; }
legend { font-weight: 650; font-size: 1.05rem; padding: 0 .5rem 0 0; }
.field { display: grid; grid-template-columns: 15rem minmax(0, 24rem) 1fr; gap: .25rem 1rem;
  align-items: start; padding: .45rem 0; }
.field label { font-weight: 550; }
.field .help { color: var(--muted); font-size: 13px; }
.field .source { color: var(--muted); font-size: 12px; }
.field.error input, .field.error select, .field.error textarea { border-color: var(--brick); }
.problems { background: var(--brick-soft); color: var(--brick); padding: .75rem 1rem;
  border-radius: 4px; margin: 0 0 1rem; }
.notice { background: var(--steel-soft); color: var(--steel); padding: .6rem 1rem;
  border-radius: 4px; margin: 0 0 1rem; }
details { margin-top: .5rem; }
summary { cursor: pointer; color: var(--steel); white-space: nowrap; }
.empty { padding: 2rem 0; color: var(--muted); }
@media (max-width: 760px) {
  .frame { grid-template-columns: 1fr; }
  nav { border-right: 0; border-bottom: 1px solid var(--rule); display: flex; gap: .5rem;
    flex-wrap: wrap; align-items: center; padding: .75rem 1rem; }
  nav .who, nav .id { margin: 0 1rem 0 0; }
  main { padding: 1.25rem 1rem 2rem; }
  .lane { grid-template-columns: 1fr; }
  .actions { justify-content: flex-start; }
  .field { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: no-preference) {
  .track span.now { animation: beat 2.4s ease-in-out infinite; }
  @keyframes beat { 50% { border-top-color: var(--steel-soft); } }
}
"""

LIVE = """
<script>
(() => {
  const live = document.getElementById("live");
  if (!live || !window.EventSource) return;
  const events = new EventSource("/events");
  events.onmessage = async () => {
    if (live.contains(document.activeElement)) return;
    const response = await fetch(location.pathname + "?fragment=1");
    if (response.ok) live.innerHTML = await response.text();
  };
})();
</script>
"""

PAGES = (
    ("/", "Issues"),
    ("/workers", "Workers"),
    ("/repositories", "Repositories"),
    ("/fleet", "Fleet"),
    ("/settings", "Settings"),
)


def e(value: object) -> str:
    return escape("" if value is None else str(value))


def span(delta: timedelta) -> str:
    minutes = int(abs(delta).total_seconds()) // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def when(moment: datetime, now: datetime) -> str:
    relative = f"in {span(moment - now)}" if moment > now else f"{span(now - moment)} ago"
    return f"{moment:%Y-%m-%d %H:%M} UTC ({relative})"


def page(title: str, path: str, snapshot_id: str, body: str, live: bool = False) -> str:
    links = "".join(
        f'<a href="{href}"{" aria-current=page" if href == path else ""}>{name}</a>'
        for href, name in PAGES
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{e(title)}: GitHubIssueWorker</title><style>{STYLE}</style></head><body>"
        f"<div class=frame><nav><div class=who>GitHubIssueWorker</div>"
        f"<div class=id>manager {e(snapshot_id)}</div>{links}</nav>"
        f"<main>{body}</main></div>{LIVE if live else ''}</body></html>"
    )


# --- workers -------------------------------------------------------------------------------------


def _track(step: str | None) -> str:
    position = STEPS.index(step) if step in STEPS else -1
    cells = []
    for index, name in enumerate(STEPS):
        css = "now" if index == position else "past" if index < position else ""
        current = ' aria-current="step"' if index == position else ""
        cells.append(f'<span class="{css}"{current}>{name}</span>')
    return f'<div class=track role=list aria-label="Steps">{"".join(cells)}</div>'


def _lane(worker: WorkerView, now: datetime) -> str:
    who = (
        f"<div class=name>{e(worker.name)}</div>"
        f"<div class=runs>{e(worker.agent)} on account <span class=nowrap>{e(worker.account)}</span></div>"
        + (
            f"<div class=model>{e(worker.model)}</div>"
            if worker.model != "-"
            else "<div class=quiet>model shown after its first run</div>"
        )
    )
    if worker.state == "working" and worker.issue is not None:
        facts = []
        if worker.branch:
            facts.append(f"branch <span class=mono>{e(worker.branch)}</span>")
        if worker.worktree:
            facts.append(f"in <span class=mono>{e(worker.worktree)}</span>")
        if worker.since:
            facts.append(f"for {span(now - worker.since)}")
        middle = (
            f"<div class=issue><span class=repo>{e(worker.repository)}</span> "
            f'<a href="https://github.com/{e(worker.repository)}/issues/{worker.issue}">'
            f"#{worker.issue} {e(worker.issue_title)}</a></div>"
            f"{_track(worker.step)}<div class=facts>{', '.join(facts)}</div>"
        )
    elif worker.state == "limited" and worker.limited_until:
        middle = (
            f'<span class="flag limited">Usage limit</span> '
            f"<span class=quiet>resets {when(worker.limited_until, now)}</span>"
        )
    elif worker.state == "unavailable":
        middle = f'<span class="flag unavailable">Unavailable</span> <span class=quiet>{e(worker.reason)}</span>'
    elif worker.state == "paused":
        middle = (
            '<span class="flag paused">Paused</span> <span class=quiet>takes no new issue</span>'
        )
    else:
        middle = "<span class=quiet>Idle, waiting for an issue.</span>"

    actions = _worker_actions(worker) + f'<a class=button href="/workers/{e(worker.name)}">Edit</a>'

    return f'<section class=lane aria-label="{e(worker.name)}"><div>{who}</div><div>{middle}</div><div class=actions>{actions}</div></section>'


def _worker_actions(worker: WorkerView, with_log: bool = True) -> str:
    """Log, pause or resume, and stop & release: what can be done to a local worker."""
    name = e(worker.name)
    toggle = "resume" if worker.state == "paused" else "pause"
    actions = []
    if worker.state == "working" and with_log:
        actions.append(f'<a class=button href="/workers/{name}/log">Log</a>')
    actions.append(
        f'<form method=post action="/workers/{name}/{toggle}"><button>{toggle.capitalize()}</button></form>'
    )
    if worker.state == "working":
        actions.append(
            f'<form method=post action="/workers/{name}/stop"><button class=danger '
            f'title="Kill the agent, give the issue back and pause {name}">Stop &amp; release</button></form>'
        )
    return "".join(actions)


def workers_fragment(snapshot: Snapshot) -> str:
    if not snapshot.workers:
        return (
            '<p class=empty>No workers yet. <a href="/workers/new">Add one</a> from the agents '
            "installed on this machine.</p>"
        )
    return "".join(_lane(worker, snapshot.now) for worker in snapshot.workers)


def workers_page(snapshot: Snapshot, notice: str = "") -> str:
    working = sum(1 for worker in snapshot.workers if worker.state == "working")
    body = (
        "<h1>Workers</h1>"
        f"<p class=lede>{working} of {len(snapshot.workers)} working. Each worker takes one issue "
        "at a time; the track shows how far its issue has come.</p>"
        f"{_notice(notice)}<div id=live>{workers_fragment(snapshot)}</div>"
        '<p><a href="/workers/new">Add a worker</a></p>'
    )
    return page("Workers", "/workers", snapshot.manager_id, body, live=True)


# --- repositories --------------------------------------------------------------------------------


def repositories_page(
    snapshot: Snapshot, config: Configuration, notice: str = "", problems: Iterable[str] = ()
) -> str:
    rows = []
    for repository in snapshot.repositories:
        counts = ", ".join(f"{count} {name}" for name, count in sorted(repository.counts.items()))
        since = repository.watching_since
        overrides = next(r for r in config.repositories if r.repository == repository.name)
        rows.append(
            f'<tr><td><a href="https://github.com/{e(repository.name)}">{e(repository.name)}</a>'
            f"{_override_form(overrides, config.defaults)}</td>"
            f"<td>{e(when(since, snapshot.now)) if since else 'not polled yet'}</td>"
            f"<td>{e(counts) or '<span class=quiet>no active issues</span>'}</td>"
            f'<td><form method=post action="/repositories/remove">'
            f'<input type=hidden name="repository" value="{e(repository.name)}">'
            f"<button class=danger>Stop watching</button></form></td></tr>"
        )
    table = (
        "<table><thead><tr><th>Repository</th><th>Watching since</th><th>Issues</th><th></th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        if rows
        else "<p class=empty>No repository is watched yet. Add one you can push to.</p>"
    )
    body = (
        "<h1>Repositories</h1>"
        "<p class=lede>New issues and comments in these repositories are worked. A repository "
        "is only added when your account can push to it.</p>"
        f"{_notice(notice)}{_problems(problems)}"
        '<form class=inline method=post action="/repositories/add">'
        "<label for=repository>Repository</label>"
        '<input id=repository name="repository" placeholder="owner/name or URL" size=36 required>'
        "<button class=primary>Watch repository</button></form>"
        f"{table}"
    )
    return page("Repositories", "/repositories", snapshot.manager_id, body)


def _override_form(repository: RepositorySettings, defaults: DefaultsSettings) -> str:
    fields = [f for f in fields_of(RepositorySettings) if f.path != "repository"]
    inputs = "".join(
        _input(
            field,
            display(getattr(repository, _attr(RepositorySettings, field.path))),
            placeholder=display(value_at(defaults, field.path)),
            source="",
        )
        for field in fields
    )
    return (
        f'<details><summary>Overrides</summary><form method=post action="/repositories/save">'
        f'<input type=hidden name="repository" value="{e(repository.repository)}">'
        f"<p class=quiet>Empty fields use the defaults from Settings.</p>{inputs}"
        "<button class=primary>Save overrides</button></form></details>"
    )


# --- fleet ---------------------------------------------------------------------------------------


def _claim_row(claim: ClaimView, now: datetime) -> str:
    state = (
        f'<span title="{e(when(claim.paused_until, now))}">paused, resumes in {span(claim.paused_until - now)}</span>'
        if claim.paused_until
        else e(claim.state)
    )
    manager, _, worker = claim.holder.rpartition("/")
    holder = (
        f"<span class=holder>{e(worker)}<span class=quiet>"
        f"{'this manager' if claim.mine else e(manager)}</span></span>"
    )
    reference = f"{claim.repository}#{claim.issue}"
    release = (
        f'<details><summary>Release</summary><form method=post action="/claims/release">'
        f'<input type=hidden name="repository" value="{e(claim.repository)}">'
        f'<input type=hidden name="issue" value="{claim.issue}">'
        + (
            ""
            if claim.mine
            else f"<label>Type <code>{e(reference)}</code> to release another manager's claim "
            f'<input name="confirm" required></label>'
        )
        + "<button class=danger>Release</button></form></details>"
    )
    return (
        f'<tr><td><a href="https://github.com/{e(claim.repository)}/issues/{claim.issue}">'
        f"#{claim.issue}</a></td><td>{holder}</td><td>{state}</td>"
        f'<td>{e(claim.step)}</td><td class=nowrap title="{claim.renewed:%Y-%m-%d %H:%M} UTC">'
        f"{span(now - claim.renewed)} ago</td>"
        f'<td class=nowrap title="{claim.expires:%Y-%m-%d %H:%M} UTC">in {span(claim.expires - now)}</td>'
        f"<td class=mono>{e(claim.branch)}</td>"
        f"<td>{release}</td></tr>"
    )


def fleet_fragment(snapshot: Snapshot) -> str:
    if not snapshot.claims:
        return "<p class=empty>No issue is claimed right now, by this or any other manager.</p>"
    parts = []
    for repository in sorted({claim.repository for claim in snapshot.claims}):
        claims = sorted(
            (c for c in snapshot.claims if c.repository == repository), key=lambda c: c.issue
        )
        rows = "".join(_claim_row(claim, snapshot.now) for claim in claims)
        parts.append(
            f"<h2>{e(repository)}</h2><table><thead><tr><th>Issue</th><th>Held by</th>"
            "<th>State</th><th>Step</th><th>Renewed</th><th>Expires</th><th>Branch</th><th></th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    return "".join(parts)


def fleet_page(snapshot: Snapshot, notice: str = "", problems: Iterable[str] = ()) -> str:
    body = (
        "<h1>Fleet</h1>"
        "<p class=lede>Every issue claimed in the watched repositories, by this manager or any "
        "other. A claim nobody renews for its validity plus grace is free for others.</p>"
        f"{_notice(notice)}{_problems(problems)}<div id=live>{fleet_fragment(snapshot)}</div>"
    )
    return page("Fleet", "/fleet", snapshot.manager_id, body, live=True)


# --- settings and worker forms -------------------------------------------------------------------


def _attr(model: type[BaseModel], alias: str) -> str:
    return next(
        (name for name, info in model.model_fields.items() if (info.alias or name) == alias), alias
    )


def _input(
    field: FormField, value: str, placeholder: str = "", source: str = "", error: bool = False
) -> str:
    name = e(field.path)
    ident = "f-" + field.path.replace(".", "-").replace("[", "-").replace("]", "")
    if field.kind == "bool":
        control = (
            f'<input type=checkbox id="{ident}" name="{name}" value=true'
            f"{' checked' if value == 'true' else ''}>"
        )
    elif field.kind == "choice":
        options = [("", f"default ({placeholder})")] if field.optional or placeholder else []
        options += [(choice, choice) for choice in field.choices]
        control = (
            f'<select id="{ident}" name="{name}">'
            + "".join(
                f'<option value="{e(v)}"{" selected" if v == value else ""}>{e(t)}</option>'
                for v, t in options
            )
            + "</select>"
        )
    elif field.kind == "env":
        control = f'<textarea id="{ident}" name="{name}" rows=3 cols=34 placeholder="NAME=value">{e(value)}</textarea>'
    elif field.kind == "secret":
        hint = "set; leave empty to keep" if value else "not set"
        control = f'<input type=password id="{ident}" name="{name}" placeholder="{hint}" autocomplete=off>'
    else:
        kind = "number" if field.kind == "number" else "text"
        step = " step=any" if field.kind == "number" else ""
        control = (
            f'<input type={kind}{step} id="{ident}" name="{name}" value="{e(value)}" '
            f'placeholder="{e(placeholder)}" size=30>'
        )
    help_text = HELP.get(field.path, "")
    note = f"<div class=source>{e(source)}</div>" if source else ""
    return (
        f'<div class="field{" error" if error else ""}"><label for="{ident}">{e(field.label)}</label>'
        f"<div>{control}{note}</div><div class=help>{e(help_text)}</div></div>"
    )


SECTIONS = (
    ("manager", "This machine"),
    ("pollInterval", "Polling and trust"),
    ("ui", "This page"),
    ("fleet", "Fleet"),
    ("defaults", "Defaults for every repository"),
)


def settings_page(
    snapshot_id: str,
    config: Configuration,
    notice: str = "",
    problems: Iterable[str] = (),
    error_paths: frozenset[str] = frozenset(),
    submitted: Mapping[str, str] | None = None,
) -> str:
    fields = fields_of(Configuration, skip=frozenset({"workers", "repositories", "accounts"}))
    defaults = Configuration()
    groups: dict[str, list[str]] = {title: [] for _, title in SECTIONS}
    for field in fields:
        head = field.path.split(".")[0]
        title = next(
            (t for key, t in SECTIONS if key == head),
            "Polling and trust",
        )
        value = (
            submitted.get(field.path, "")
            if submitted is not None
            else display(value_at(config, field.path))
        )
        if field.kind == "bool" and submitted is not None:
            value = "true" if field.path in submitted else "false"
        if field.kind == "secret":
            value = "set" if config.ui.token else ""
        source = "set in the configuration" if is_set(config, field.path) else "default"
        groups[title].append(
            _input(
                field,
                value,
                display(value_at(defaults, field.path)),
                source,
                field.path in error_paths,
            )
        )
    body = "".join(
        f"<fieldset><legend>{e(title)}</legend>{''.join(inputs)}</fieldset>"
        for title, inputs in groups.items()
        if inputs
    )
    accounts = _accounts(config)
    page_body = (
        "<h1>Settings</h1>"
        "<p class=lede>Everything here is also in the configuration file; saving writes the file "
        "and applies the change without a restart. Workers and repositories have their own pages."
        f"</p>{_notice(notice)}{_problems(problems)}"
        f'<form method=post action="/settings">{body}{accounts}'
        "<p><button class=primary>Save settings</button></p></form>"
    )
    return page("Settings", "/settings", snapshot_id, page_body)


def _accounts(config: Configuration) -> str:
    rows = []
    for index, (name, account) in enumerate([*config.accounts.items(), ("", AccountSettings())]):
        runs = "" if account.max_concurrent_runs is None else str(account.max_concurrent_runs)
        rows.append(
            f'<tr><td><input name="accounts[{index}].name" value="{e(name)}" '
            f'placeholder="{"new account" if not name else ""}" aria-label="Account name"></td>'
            f'<td><input type=number min=1 name="accounts[{index}].maxConcurrentRuns" value="{e(runs)}" '
            'placeholder="unlimited" aria-label="Runs at once"></td>'
            + (
                f'<td><label><input type=checkbox name="accounts[{index}].remove" value=true> remove</label></td>'
                if name
                else "<td></td>"
            )
            + "</tr>"
        )
    return (
        "<fieldset><legend>Accounts</legend>"
        "<p class=quiet>What workers bill against. Usage limits are tracked per account; a local "
        "model that serves one request at a time gets 1 run at once.</p>"
        "<table><thead><tr><th>Name</th><th>Runs at once</th><th></th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></fieldset>"
    )


WORKER_FIELDS = [f for f in fields_of(WorkerSettings) if not f.path.startswith("installation")]
INSTALLATION_FIELDS = ("type", "path", "env", "root", "profile")


def worker_page(
    snapshot_id: str,
    config: Configuration,
    worker: WorkerSettings | None,
    detected: list[dict[str, Any]] | None = None,
    problems: Iterable[str] = (),
) -> str:
    title = f"Worker {worker.name}" if worker else "Add a worker"
    current: dict[str, str] = {}
    if worker:
        for field in WORKER_FIELDS:
            current[field.path] = display(getattr(worker, _attr(WorkerSettings, field.path)))
        for key in INSTALLATION_FIELDS:
            current[f"installation.{key}"] = display(getattr(worker.installation, key, None))
    inputs = []
    for field in WORKER_FIELDS:
        if field.path == "account":
            options = "".join(
                f"<option{' selected' if name == current.get('account') else ''}>{e(name)}</option>"
                for name in config.accounts
            )
            inputs.append(
                f"<div class=field><label for=f-account>Account</label><div><select id=f-account "
                f'name="account">{options}</select></div><div class=help>Add accounts under Settings.</div></div>'
            )
            continue
        inputs.append(_input(field, current.get(field.path, "")))
    install_type = current.get("installation.type", "path")
    install = [
        _input(
            FormField(
                "installation.type",
                "Installation",
                "choice",
                ("path", "executable", "claudecodeportable", "claudelocal"),
            ),
            install_type,
        ),
        _input(
            FormField("installation.path", "Executable path", "text", optional=True),
            current.get("installation.path", ""),
            "for executable",
        ),
        _input(
            FormField("installation.env", "Environment", "env", optional=True),
            current.get("installation.env", ""),
        ),
        _input(
            FormField("installation.root", "Root folder", "text", optional=True),
            current.get("installation.root", ""),
            "~/ClaudeCodePortable or ~/ClaudeLocal",
        ),
        _input(
            FormField("installation.profile", "Profile", "text", optional=True),
            current.get("installation.profile", ""),
            "for claudecodeportable",
        ),
    ]
    action = f"/workers/{e(worker.name)}" if worker else "/workers/new"
    found = ""
    if detected:
        rows = "".join(
            f"<tr><td>{e(item['name'])}</td><td>{e(item['agent'])}</td>"
            f"<td>{e(item['installation']['type'])}</td>"
            f'<td><form method=post action="/workers/add-detected"><input type=hidden name="index" value={index}>'
            "<button>Add</button></form></td></tr>"
            for index, item in enumerate(detected)
        )
        found = (
            "<h2>Found on this machine</h2><table><thead><tr><th>Name</th><th>Agent</th>"
            f"<th>Installation</th><th></th></tr></thead><tbody>{rows}</tbody></table>"
            "<h2>Or describe one</h2>"
        )
    remove = (
        f'<form method=post action="/workers/{e(worker.name)}/remove"><button class=danger>'
        "Remove worker</button> <span class=quiet>Its issue is released for other workers."
        "</span></form>"
        if worker
        else ""
    )
    body = (
        f"<h1>{e(title)}</h1>{_problems(problems)}{found}"
        f'<form method=post action="{action}"><fieldset><legend>Agent</legend>{"".join(inputs)}</fieldset>'
        f"<fieldset><legend>Installation</legend>{''.join(install)}</fieldset>"
        "<p><button class=primary>Save worker</button></p></form>"
        f"{remove}"
    )
    return page(title, "/", snapshot_id, body)


def _notice(text: str) -> str:
    return f"<p class=notice role=status>{e(text)}</p>" if text else ""


def _problems(problems: Iterable[str]) -> str:
    items = list(problems)
    if not items:
        return ""
    lines = "".join(f"<li>{e(problem)}</li>" for problem in items)
    return f"<div class=problems role=alert><strong>Not saved.</strong><ul>{lines}</ul></div>"


# --- issues --------------------------------------------------------------------------------------


def _issue_row(issue: IssueView, workers: Mapping[str, WorkerView], now: datetime) -> str:
    link = (
        f"<span class=quiet>{e(issue.repository)}</span><br>"
        f'<a href="https://github.com/{e(issue.repository)}/issues/{issue.number}">'
        f"#{issue.number} {e(issue.title)}</a>"
    )
    if issue.worker:
        where = "this machine" if issue.local else f"{issue.manager}, remote"
        model = f" on {e(issue.model)}" if issue.model else ""
        who = (
            f"{e(issue.worker)}{model}"
            f'<span class="where{" local" if issue.local else ""}">{e(where)}</span>'
        )
    else:
        who = "<span class=quiet>nobody</span>"
    since = f" for {span(now - issue.since)}" if issue.since else ""
    step = f"{e(issue.step)}{since}" if issue.step else ""
    worker = workers.get(issue.worker or "") if issue.local else None
    if worker is not None:
        actions = f"<div class=actions>{_worker_actions(worker)}</div>"
    elif issue.worker:
        actions = '<a href="/fleet">Fleet</a>'
    else:
        actions = ""
    return (
        f"<tr><td>{link}</td><td>{e(issue.state)}</td><td>{who}</td><td class=nowrap>{step}</td>"
        f"<td>{actions}</td></tr>"
    )


def issues_fragment(snapshot: Snapshot) -> str:
    if not snapshot.issues:
        return (
            "<p class=empty>No open issue in the watched repositories. "
            '<a href="/repositories">Watch another repository</a>.</p>'
        )
    workers = {worker.name: worker for worker in snapshot.workers}
    order = sorted(
        snapshot.issues, key=lambda i: (not i.local, i.worker is None, i.repository, i.number)
    )
    rows = "".join(_issue_row(issue, workers, snapshot.now) for issue in order)
    return (
        "<table><thead><tr><th>Issue</th><th>State</th><th>Worked by</th><th>Step</th><th></th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def issues_page(snapshot: Snapshot, notice: str = "", problems: Iterable[str] = ()) -> str:
    here = sum(1 for issue in snapshot.issues if issue.local)
    elsewhere = sum(1 for issue in snapshot.issues if issue.worker and not issue.local)
    body = (
        "<h1>Issues</h1>"
        f"<p class=lede>{len(snapshot.issues)} open in the watched repositories: {here} worked "
        f"on this machine, {elsewhere} by other managers. Workers on this machine can be paused, "
        "stopped or watched.</p>"
        f"{_notice(notice)}{_problems(problems)}<div id=live>{issues_fragment(snapshot)}</div>"
    )
    return page("Issues", "/", snapshot.manager_id, body, live=True)


# --- the agent's log -----------------------------------------------------------------------------


def _entry(entry: Entry) -> str:
    if entry.kind == "tool":
        return f"<li class=tool><strong>{e(entry.title)}</strong>{e(entry.text)}</li>"
    if entry.kind == "output":
        return f"<li><pre>{e(entry.text)}</pre></li>"
    return f'<li class="{entry.kind}">{e(entry.text)}</li>'


def log_fragment(text: str | None) -> str:
    if text is None:
        return "<p class=empty>This worker has not run yet, so there is no log.</p>"
    entries = transcript(text)
    if not entries:
        return "<p class=empty>The agent has started; its first output appears here.</p>"
    return f"<ol class=log>{''.join(_entry(entry) for entry in entries)}</ol>"


def log_page(snapshot: Snapshot, worker: WorkerView, text: str | None) -> str:
    about = f" on #{worker.issue} {e(worker.issue_title)}" if worker.issue is not None else ""
    body = (
        f"<h1>{e(worker.name)}{about}</h1>"
        f"<p class=lede>What {e(worker.agent)} on {e(worker.model)} says and does, as it "
        "happens: messages, the commands and tools it runs, and their output.</p>"
        f'<div class=actions style="justify-content:flex-start;margin-bottom:1rem">{_worker_actions(worker, with_log=False)}</div>'
        f"<div id=live>{log_fragment(text)}</div>"
    )
    return page(f"Log of {worker.name}", "/workers", snapshot.manager_id, body, live=True)
