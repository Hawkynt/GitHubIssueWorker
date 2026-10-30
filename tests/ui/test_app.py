from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from github_issue_worker.configuration import Configuration, RepositorySettings, WorkerSettings
from github_issue_worker.ui.app import create_app
from github_issue_worker.ui.demo import DemoBackend


@pytest.fixture
def backend() -> DemoBackend:
    return DemoBackend()


@pytest.fixture
def client(backend: DemoBackend) -> TestClient:
    return TestClient(create_app(backend))


def names(html: str) -> set[str]:
    return set(re.findall(r'name="([^"]+)"', html))


def schema_paths(model: type[BaseModel], prefix: str = "") -> set[str]:
    found: set[str] = set()
    for name, info in model.model_fields.items():
        alias = info.alias or name
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            found |= schema_paths(annotation, f"{prefix}{alias}.")
        else:
            found.add(f"{prefix}{alias}")
    return found


def test_every_page_renders(client: TestClient) -> None:
    for path in (
        "/",
        "/workers",
        "/workers/claude/log",
        "/repositories",
        "/fleet",
        "/settings",
        "/workers/new",
        "/workers/claude",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "<nav>" in response.text


def test_the_workers_page_shows_who_works_what_on_which_model_and_step(client: TestClient) -> None:
    html = client.get("/workers").text
    assert "Claude Opus 5.5" in html and "GPT 6.1 Sol" in html
    assert "#214 Retry uploads after a gateway timeout" in html
    assert 'class="now" aria-current="step">verify<' in html
    assert "issue/214-retry-uploads-after-a-gateway-timeout" in html
    assert "~/.cache/GitHubIssueWorker/acme/telemetry-gateway/issues/214" in html
    assert "Usage limit" in html and "opencode is not on PATH" in html
    assert "model shown after its first run" in html  # opencode has never run


def test_live_fragments_carry_only_the_changing_part(client: TestClient) -> None:
    fragment = client.get("/workers?fragment=1").text
    assert "<nav>" not in fragment and "Claude Opus 5.5" in fragment
    assert "laptop-01ab</span>" in client.get("/fleet?fragment=1").text


def test_every_configuration_key_has_a_field_on_some_page(client: TestClient) -> None:
    # F14.8: a key added to the schema without a UI field fails here.
    settings = names(client.get("/settings").text)
    worker = names(client.get("/workers/claude").text)
    repository = names(client.get("/repositories").text)
    top = schema_paths(Configuration) - {"workers", "repositories", "accounts"}
    assert top - settings == set()
    worker_keys = schema_paths(WorkerSettings) - {"installation"}
    assert worker_keys - worker == set()
    assert {
        "installation.type",
        "installation.path",
        "installation.env",
        "installation.root",
        "installation.profile",
    } <= worker
    assert schema_paths(RepositorySettings) - repository == set()
    assert {"accounts[0].name", "accounts[0].maxConcurrentRuns"} <= settings


def test_saving_settings_writes_only_what_changed(client: TestClient, backend: DemoBackend) -> None:
    form = _settings_form(client)
    form["pollInterval"] = "00:02:30"
    response = client.post("/settings", data=form, follow_redirects=False)
    assert response.status_code == 303
    config = backend.config()
    assert config.poll_interval.total_seconds() == 150
    assert not config.fleet.is_set("claim_grace")
    assert config.accounts["local-gpu"].max_concurrent_runs == 1


def test_an_invalid_setting_is_refused_with_its_path(
    client: TestClient, backend: DemoBackend
) -> None:
    before = backend.config()
    form = _settings_form(client)
    form["fleet.claimValidity"] = "a week"
    response = client.post("/settings", data=form)
    assert response.status_code == 422
    assert "fleet.claimValidity" in response.text and "Not saved." in response.text
    assert 'value="a week"' in response.text  # what was typed stays in the form
    assert backend.config() is before


def test_the_token_is_write_only(client: TestClient) -> None:
    form = _settings_form(client)
    form["ui.token"] = "s3cret-value"
    form["ui.bind"] = "0.0.0.0"
    client.post("/settings", data=form)
    html = client.get("/settings?token=s3cret-value").text
    assert "s3cret-value" not in html


def test_a_non_loopback_bind_without_a_token_is_refused(client: TestClient) -> None:
    form = _settings_form(client)
    form["ui.bind"] = "0.0.0.0"
    response = client.post("/settings", data=form)
    assert response.status_code == 422 and "ui.token" in response.text


def test_with_a_token_every_request_must_carry_it(client: TestClient, backend: DemoBackend) -> None:
    form = _settings_form(client)
    form["ui.token"] = "s3cret-value"
    client.post("/settings", data=form)
    fresh = TestClient(create_app(backend))
    assert fresh.get("/").status_code == 401
    assert fresh.get("/?token=wrong").status_code == 401
    assert fresh.get("/?token=s3cret-value").status_code == 200
    assert fresh.get("/fleet").status_code == 200  # the cookie carries it from now on


def test_adding_a_repository_checks_push_access(client: TestClient, backend: DemoBackend) -> None:
    response = client.post(
        "/repositories/add",
        data={"repository": "https://github.com/acme/new-thing"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "acme/new-thing" in {r.repository for r in backend.config().repositories}

    refused = client.post("/repositories/add", data={"repository": "someone-else/thing"})
    assert refused.status_code == 422 and "cannot push" in refused.text
    assert "someone-else/thing" not in {r.repository for r in backend.config().repositories}


def test_the_add_box_suggests_owners_then_their_unwatched_repositories(
    client: TestClient,
) -> None:
    # F14.16: the demo can push to acme/*, acme-labs/* and hana-ito/*; acme/design-tokens and two
    # more acme repositories are watched already.
    owners = client.get("/repositories/suggest", params={"q": "ac"})
    assert owners.status_code == 200
    assert owners.json() == {"kind": "owner", "items": ["acme", "acme-labs"]}
    repositories = client.get("/repositories/suggest", params={"q": "acme/"}).json()
    assert repositories["kind"] == "repository"
    assert "acme/ingest-worker" in repositories["items"]
    assert "acme/design-tokens" not in repositories["items"]
    assert client.get("/repositories/suggest", params={"q": "ACME/ing"}).json()["items"] == [
        "acme/ingest-worker"
    ]
    assert client.get("/repositories/suggest", params={"q": "someone-else/"}).json()["items"] == []
    assert client.get("/repositories/suggest").json() == {"kind": "owner", "items": []}


def test_the_add_box_is_a_combobox_that_free_text_still_works_in(client: TestClient) -> None:
    html = client.get("/repositories").text
    box = re.search(r"<input id=repository[^>]*>", html)
    assert box is not None
    for attribute in ("role=combobox", "aria-autocomplete=list", "aria-controls=repository-list"):
        assert attribute in box.group(0)
    assert "autocomplete=off" in box.group(0) and "required" in box.group(0)
    assert "<ul id=repository-list role=listbox" in html
    assert "/repositories/suggest" in html


def test_a_suggested_repository_is_added_through_the_verified_add(
    client: TestClient, backend: DemoBackend
) -> None:
    picked = client.get("/repositories/suggest", params={"q": "hana-ito/"}).json()["items"][0]
    response = client.post("/repositories/add", data={"repository": picked}, follow_redirects=False)
    assert response.status_code == 303
    assert picked in {r.repository for r in backend.config().repositories}
    assert (
        picked not in client.get("/repositories/suggest", params={"q": "hana-ito/"}).json()["items"]
    )


def test_with_a_token_the_suggestions_need_it_too(client: TestClient, backend: DemoBackend) -> None:
    form = _settings_form(client)
    form["ui.token"] = "s3cret-value"
    client.post("/settings", data=form)
    fresh = TestClient(create_app(backend))
    assert fresh.get("/repositories/suggest", params={"q": "ac"}).status_code == 401
    allowed = fresh.get("/repositories/suggest", params={"q": "ac", "token": "s3cret-value"})
    assert allowed.status_code == 200 and allowed.json()["items"] == ["acme", "acme-labs"]


def test_repository_overrides_and_removal(client: TestClient, backend: DemoBackend) -> None:
    client.post("/repositories/save", data={"repository": "acme/design-tokens", "test": "npm test"})
    saved = next(r for r in backend.config().repositories if r.repository == "acme/design-tokens")
    assert saved.test == "npm test" and saved.mode is None
    client.post("/repositories/remove", data={"repository": "acme/design-tokens"})
    assert "acme/design-tokens" not in {r.repository for r in backend.config().repositories}


def test_workers_can_be_added_edited_paused_and_removed(
    client: TestClient, backend: DemoBackend
) -> None:
    client.post("/workers/add-detected", data={"index": "0"})
    assert "codex-2" in {w.name for w in backend.config().workers}

    form = {
        "name": "codex-2",
        "agent": "codex",
        "account": "openai-plus",
        "priority": "4",
        "model": "gpt-6.1-sol",
        "installation.type": "path",
    }
    client.post("/workers/codex-2", data=form)
    edited = next(w for w in backend.config().workers if w.name == "codex-2")
    assert (edited.priority, edited.model, edited.installation.type) == (4, "gpt-6.1-sol", "path")

    client.post("/workers/local/pause")
    assert "Paused" in client.get("/workers").text
    client.post("/workers/codex-2/remove")
    assert "codex-2" not in {w.name for w in backend.config().workers}


def test_an_invalid_worker_is_refused(client: TestClient) -> None:
    form = {
        "name": "x",
        "agent": "claude",
        "account": "no-such-account",
        "installation.type": "path",
    }
    response = client.post("/workers/new", data=form)
    assert response.status_code == 422 and "no account" in response.text


def test_releasing_another_managers_claim_needs_typed_confirmation(client: TestClient) -> None:
    wrong = client.post(
        "/claims/release", data={"repository": "acme/billing-cli", "issue": "91", "confirm": "yes"}
    )
    assert wrong.status_code == 422
    right = client.post(
        "/claims/release",
        data={"repository": "acme/billing-cli", "issue": "91", "confirm": "acme/billing-cli#91"},
        follow_redirects=False,
    )
    assert right.status_code == 303


def _settings_form(client: TestClient) -> dict[str, str]:
    """The settings form as a browser would submit it unchanged."""
    html = client.get("/settings").text
    form: dict[str, str] = {}
    for tag in re.findall(r"<(?:input|select|textarea)[^>]*>(?:[^<]*</textarea>)?", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        if "type=checkbox" in tag:
            if " checked" in tag:
                form[name.group(1)] = "true"
            continue
        value = re.search(r'value="([^"]*)"', tag)
        form[name.group(1)] = value.group(1) if value else ""
    for select in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option value="([^"]*)" selected', select.group(2))
        form[select.group(1)] = chosen.group(1) if chosen else ""
    return form


def test_the_issues_page_lists_open_issues_with_their_local_or_remote_worker(
    client: TestClient,
) -> None:
    html = client.get("/").text
    assert "<h1>Issues</h1>" in html
    assert "Retry uploads after a gateway timeout" in html  # worked here
    assert "Buffer metrics while offline" in html  # worked by another manager
    assert "Export statements as CSV" in html
    assert "Rename the colour tokens" in html  # nobody on it yet
    local = html[html.index("Retry uploads") : html.index("Retry uploads") + 1500]
    assert "this machine" in local and "Claude Opus 5.5" in local and "Stop &amp; release" in local
    remote = html[html.index("Buffer metrics") : html.index("Buffer metrics") + 1500]
    assert "laptop-01ab" in remote and "Stop &amp; release" not in remote
    assert "waiting for your answer" in html


def test_stop_and_release_kills_the_agent_and_frees_the_issue(
    client: TestClient, backend: DemoBackend
) -> None:
    response = client.post("/workers/claude/stop", follow_redirects=False)
    assert response.status_code == 303
    assert backend.stopped == ["claude"]
    assert "Paused" in client.get("/workers").text  # it takes nothing new until resumed


def test_stopping_an_idle_worker_says_so(client: TestClient) -> None:
    response = client.post("/workers/local/stop")
    assert response.status_code == 422 and "not working on an issue" in response.text


def test_the_log_page_shows_the_agents_chat(client: TestClient) -> None:
    html = client.get("/workers/claude/log").text
    assert "<h1>claude" in html and "#214" in html
    assert "uploads.py" in html  # a tool call
    assert "1 failed" in html  # its output
    assert "Retries now back off" in html  # a message
    assert "Retries now back off" in client.get("/workers/claude/log?fragment=1").text
    assert "has not run yet" in client.get("/workers/opencode/log").text


# --- F14.12: values not yet saved are marked ------------------------------------------------------


def _controls(html: str) -> dict[str, str]:
    """Each named control of the settings form, by name, as its opening tag."""
    form = html[html.index('action="/settings"') :]
    return {
        name.group(1): tag
        for tag in re.findall(r"<(?:input|select|textarea)[^>]*>", form)
        if (name := re.search(r'name="([^"]+)"', tag))
    }


def _marked(html: str) -> set[str]:
    return {
        name for name, tag in _controls(html).items() if re.search(r'class="[^"]*\bchanged\b', tag)
    }


def test_a_freshly_loaded_settings_page_marks_nothing_as_changed(client: TestClient) -> None:
    controls = _controls(client.get("/settings").text)
    assert _marked(client.get("/settings").text) == set()
    assert {name for name, tag in controls.items() if "data-saved=" not in tag} == set()
    assert 'data-saved="00:01:00"' in controls["pollInterval"]
    assert 'data-saved="1"' in controls["accounts[3].maxConcurrentRuns"]


def test_the_settings_page_carries_the_script_that_marks_edited_values(
    client: TestClient,
) -> None:
    html = client.get("/settings").text
    assert "<script>" in html and "data-saved" in html and "changed" in html
    assert ".changed" in html  # the style that gives a marked value its own background


def test_a_refused_save_keeps_the_typed_values_marked_as_not_saved(
    client: TestClient,
) -> None:
    form = _settings_form(client)
    form["pollInterval"] = "00:02:30"
    form["fleet.claimValidity"] = "a week"
    response = client.post("/settings", data=form)
    assert response.status_code == 422
    assert _marked(response.text) == {"pollInterval", "fleet.claimValidity"}
    assert 'class="field error"' in response.text  # the error border stays as well


def test_a_successful_save_marks_nothing_as_changed(client: TestClient) -> None:
    form = _settings_form(client)
    form["pollInterval"] = "00:02:30"
    response = client.post("/settings", data=form)
    assert response.status_code == 200 and "Settings saved" in response.text
    assert _marked(response.text) == set()


def test_the_cockpit_reads_out_the_long_term_numbers(client: TestClient) -> None:
    html = client.get("/workers").text
    assert "<h1>Cockpit</h1>" in html and ">Cockpit</a>" in html
    for readout in (
        "Merged today",
        "Last 7 days",
        "All time",
        "Median time to merge",
        "Merge rate",
    ):
        assert readout in html, readout
    assert html.count('class="seg s') > 30  # the throughput and model-mix columns
    assert 'class="bar"' in html  # time to merge
    assert "Qwen3.8 Flash Next IQ3_S" in html and "Gemini 4 Argon (Medium)" in html
    assert html.count("<table") >= 4  # every chart can be read as a table


def test_the_cockpit_splits_by_model_agent_or_repository(client: TestClient) -> None:
    by_agent = client.get("/workers?by=agent&time=repository").text
    assert "aria-current=true>Agent<" in by_agent
    legend = (
        by_agent.split('id="throughput"', 1)[1].split('class="legend"', 1)[1].split("</ul>", 1)[0]
    )
    assert "antigravity" in legend and "Claude Opus 5.5" not in legend
    assert "acme/billing-cli" in by_agent.split('id="time"', 1)[1]
    assert client.get("/workers?by=nonsense").status_code == 422


def test_an_entitys_colour_survives_a_change_of_view(client: TestClient) -> None:
    def slot(html: str, name: str) -> str:
        found = re.search(rf'<li><i class="key (s\d)"></i>{re.escape(name)}<', html)
        assert found, name
        return found.group(1)

    month = client.get("/workers").text
    quarter = client.get("/workers?days=90").text
    assert slot(month, "GPT 6.1 Sol") == slot(quarter, "GPT 6.1 Sol")


def test_the_cockpit_fragment_keeps_the_chosen_view(client: TestClient) -> None:
    fragment = client.get("/workers?fragment=1&by=repository").text
    assert "<nav>" not in fragment and "acme/design-tokens" in fragment
    assert "Claude Opus 5.5" in fragment  # the crew lanes stay live too


def _square(html: str, repository: str) -> str:
    found = re.search(rf'<li><i class="key (s\d)"></i>{re.escape(repository)}<', html)
    assert found, repository
    return found.group(1)


def test_the_crew_shows_each_issue_as_a_square_coloured_by_its_repository(
    client: TestClient,
) -> None:
    html = client.get("/workers").text
    crew = html.split('aria-label="Crew"', 1)[1].split("</section>", 1)[0]
    gateway = _square(crew, "acme/telemetry-gateway")
    assert f'<i class="square {gateway}"' in crew.split("#214", 1)[0].rsplit("class=run", 1)[1]
    assert 'href="https://github.com/acme/telemetry-gateway/issues/214"' in crew
    assert 'data-tip="Uploads to the gateway fail' in crew  # the description as tooltip
    assert 'action="/workers/claude/open"' in crew  # the worktree, named like its branch
    assert ">issue/214-retry-uploads-after-a-gateway-timeout<" in crew


def test_remote_workers_get_their_own_row_with_github_branch_links_and_no_controls(
    client: TestClient,
) -> None:
    crew = client.get("/workers").text.split('aria-label="Crew"', 1)[1].split("</section>", 1)[0]
    remote = crew.split('aria-label="Remote"', 1)[1]
    assert crew.count('aria-label="Remote"') == 1  # one row, a sub-row per issue held elsewhere
    assert len(re.findall(r'<div class=(?:"run(?: waiting)?"|run)>', remote)) == 2
    assert "laptop-01ab" in remote and "ci-runner-03" in remote
    assert "#219 Buffer metrics while offline" in remote
    assert (
        'href="https://github.com/acme/telemetry-gateway/tree/issue/219-buffer-metrics-while-offline"'
        in remote
    )
    assert "<button" not in remote and "/open" not in remote
    assert f'<i class="square {_square(crew, "acme/billing-cli")}"' in remote  # same colour


def test_opening_a_worktree_hands_it_to_the_file_manager(
    client: TestClient, backend: DemoBackend
) -> None:
    response = client.post("/workers/claude/open", headers={"referer": "http://testserver/workers"})
    assert response.status_code == 200 and backend.opened == ["claude"]
    assert "is not working on an issue" in client.post("/workers/local/open").text


def test_a_worker_row_has_a_sub_row_per_instance_its_account_allows(client: TestClient) -> None:
    crew = client.get("/workers").text.split('aria-label="Crew"', 1)[1].split("</section>", 1)[0]
    claude = re.split(
        r'<div class=(?:"lane[^\"]*"|lane)', crew.split('aria-label="claude"', 1)[1], maxsplit=1
    )[0]
    assert (
        len(re.findall(r'<div class=(?:"run(?: waiting)?"|run)>', claude)) == 3
    )  # anthropic-max runs three at once
    assert "2 of 3 running" in claude
    assert "#214 Retry uploads" in claude and "#57 Export tokens" in claude
    assert 'action="/workers/claude.2/stop"' in claude  # each instance stops on its own
    assert claude.count('action="/workers/claude/pause"') == 1  # the worker pauses as a whole
    codex = re.split(
        r'<div class=(?:"lane[^\"]*"|lane)', crew.split('aria-label="codex"', 1)[1], maxsplit=1
    )[0]
    assert (
        len(re.findall(r'<div class=(?:"run(?: waiting)?"|run)>', codex)) == 2
        and "1 of 2 running" in codex
    )
    assert crew.count('aria-label="local"') == 1


def test_no_remote_row_without_issues_held_elsewhere(backend: DemoBackend) -> None:
    from github_issue_worker.ui import pages

    snapshot = backend.snapshot()
    alone = replace(snapshot, claims=tuple(c for c in snapshot.claims if c.mine))
    assert 'aria-label="Remote"' not in pages.cockpit_fragment(alone, [])


def test_live_fragments_carry_the_managers_clock_and_ticking_times(client: TestClient) -> None:
    for path in ("/workers?fragment=1", "/?fragment=1", "/fleet?fragment=1"):
        html = client.get(path).text
        assert 'data-now="2026-10-30T14:20:00+00:00"' in html, path
        assert "data-since=" in html, path
    cockpit = client.get("/workers").text
    assert 'data-since="2026-10-30T13:57:00+00:00">23m<' in cockpit  # claude on #214 for 23m
    assert "data-until=" in cockpit  # antigravity's usage limit counts down
    assert "<time data-clock>" in cockpit
