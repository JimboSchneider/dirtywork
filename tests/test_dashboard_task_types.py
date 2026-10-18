"""Task labels are editable metadata; comparison evidence uses full task briefs."""
import http.client
import json
import os
import threading
from datetime import datetime, timezone

import pytest

from dirtywork import rundir
from dirtywork.dashboard import make_server
from dirtywork.dashboard_data import RunStore


NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def record(root, slug, metadata, events=()):
    directory = root / slug
    directory.mkdir()
    (directory / "run.json").write_text(json.dumps(metadata))
    (directory / "transcript.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events))
    return directory


def test_detail_and_list_include_suggested_type_without_persisting_it(tmp_path):
    directory = record(tmp_path, "parser", {"task": "Fix the parser crash.", "status": "completed"})
    before = (directory / "run.json").read_bytes()
    store = RunStore(tmp_path)
    detail = store.detail("parser", now=NOW)
    assert detail["task_type"]["id"] == "bugfix"
    assert detail["task_type"]["source"] == "suggested"
    assert detail["task_type"]["label"]
    snapshot = store.snapshot(now=NOW)
    assert snapshot["runs"][0]["task_type"] == detail["task_type"]
    assert {option["id"] for option in snapshot["task_type_options"]} == {
        "prescribed", "bugfix", "feature", "tests", "docs", "refactor", "maintenance", "other", "demo"}
    assert (directory / "run.json").read_bytes() == before


def test_classification_and_distinct_counts_use_full_brief_without_public_leak(tmp_path):
    prefix = "Background context. " * 30
    record(tmp_path, "tail-fix", {"task": prefix + "Fix the parser crash.", "status": "completed"})
    for slug, suffix, verdict, seconds in (("first", "Add alpha support.", "accept", 10),
                                           ("second", "Add beta support.", "reject", 30)):
        record(tmp_path, slug, {"task": prefix + suffix, "status": "completed", "task_type": "feature",
                                "verdict": verdict, "review_seconds": seconds})
    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    by_slug = {run["slug"]: run for run in snapshot["runs"]}
    assert by_slug["tail-fix"]["task_type"]["id"] == "bugfix"
    assert by_slug["first"]["task"] == by_slug["second"]["task"] == prefix[:240]
    assert all(len(run["task"]) <= 240 for run in snapshot["runs"])
    assert "Add alpha support." not in json.dumps(snapshot)
    group = next(group for group in snapshot["offloading"]["groups"] if group["id"] == "feature")
    assert group["distinct_tasks"] == 2
    assert group["distinct_reviewed_tasks"] == 2
    assert group["distinct_timed_tasks"] == 2
    assert group["reviewed"] == 2
    assert group["accepted"] == 1
    assert group["median_review_s"] == 20
    assert snapshot["summary"]["total"] == 3
    assert snapshot["summary"]["reviewed"] == 2


def test_classification_reads_past_detail_preview_without_exposing_full_brief(tmp_path):
    task = "Background context. " * 4000 + "Fix the parser crash."
    directory = record(tmp_path, "long-fix", {"task": task, "status": "completed"})
    original = (directory / "run.json").read_bytes()
    store = RunStore(tmp_path)
    detail = store.detail("long-fix", now=NOW)
    assert detail["task_type"]["id"] == "bugfix"
    assert detail["task"] == task[:64000]
    snapshot = store.snapshot(now=NOW)
    assert snapshot["runs"][0]["task_type"]["id"] == "bugfix"
    assert snapshot["runs"][0]["task"] == task[:240]
    assert "Fix the parser crash." not in json.dumps(detail)
    assert "Fix the parser crash." not in json.dumps(snapshot)
    assert (directory / "run.json").read_bytes() == original


def test_distinct_evidence_uses_brief_tails_beyond_detail_preview(tmp_path):
    prefix = "Background context. " * 4000
    for slug, suffix in (("alpha", "Add alpha support."), ("beta", "Add beta support.")):
        record(tmp_path, slug, {"task": prefix + suffix, "status": "completed", "task_type": "feature",
                               "verdict": "accept", "review_seconds": 10})
    store = RunStore(tmp_path)
    snapshot = store.snapshot(now=NOW)
    group = snapshot["offloading"]["groups"][0]
    assert group["distinct_tasks"] == 2
    assert group["distinct_reviewed_tasks"] == 2
    assert group["distinct_timed_tasks"] == 2
    assert group["review_samples"] == 2
    assert store.detail("alpha", now=NOW)["task"] == prefix[:64000]
    assert all(run["task"] == prefix[:240] for run in snapshot["runs"])
    assert "Add alpha support." not in json.dumps(snapshot)
    assert "Add beta support." not in json.dumps(snapshot)


@pytest.mark.parametrize("metadata", [
    {"status": "running", "host_pid": os.getpid()},
    {"status": "completed"},
    {"status": "completed", "verdict": "accept", "review_seconds": 42,
     "review_started_at": "2026-10-18T10:00:00Z", "verdict_at": "2026-10-18T10:00:42Z", "note": "Reviewed"},
    {"status": "completed", "resumed_by": "follow-up"},
])
def test_type_edit_preserves_lifecycle_review_and_unknown_metadata(tmp_path, metadata):
    original = {**metadata, "task": "Fix the parser crash.", "custom": {"keep": [1, 2, 3]}}
    directory = record(tmp_path, "editable", original)
    result = RunStore(tmp_path).set_task_type("editable", "docs")
    assert result["task_type"]["id"] == "docs"
    assert result["task_type"]["source"] == "manual"
    assert json.loads((directory / "run.json").read_text()) == {**original, "task_type": "docs"}


def test_reset_removes_override_and_snapshot_refreshes_after_external_metadata_edit(tmp_path):
    directory = record(tmp_path, "editable", {"task": "Fix the parser crash.", "task_type": "feature"})
    store = RunStore(tmp_path)
    assert store.snapshot(now=NOW)["runs"][0]["task_type"]["id"] == "feature"
    reset = store.set_task_type("editable", None)
    assert reset["task_type"]["id"] == "bugfix"
    assert reset["task_type"]["source"] == "suggested"
    assert "task_type" not in json.loads((directory / "run.json").read_text())
    (directory / "run.json").write_text(json.dumps({"task": "Fix the parser crash.", "task_type": "tests"}))
    assert store.snapshot(now=NOW)["runs"][0]["task_type"] == {
        "id": "tests", "label": "Tests", "source": "manual"}


def test_reset_does_not_restore_an_override_from_the_start_event(tmp_path):
    record(tmp_path, "legacy", {"task_type": "docs"}, [
        {"event": "run_start", "task": "Fix the parser crash.", "task_type": "feature"},
    ])
    run = RunStore(tmp_path).set_task_type("legacy", None)
    assert run["task"] == "Fix the parser crash."
    assert run["task_type"]["id"] == "bugfix"
    assert run["task_type"]["source"] == "suggested"


@pytest.mark.parametrize("task_type", ["invalid", "", "BUGFIX", True, 1, [], {}])
def test_store_rejects_invalid_type_without_changing_metadata(tmp_path, task_type):
    directory = record(tmp_path, "editable", {"task": "Fix the parser crash."})
    before = (directory / "run.json").read_bytes()
    with pytest.raises(ValueError):
        RunStore(tmp_path).set_task_type("editable", task_type)
    assert (directory / "run.json").read_bytes() == before


def test_type_edit_detects_concurrent_metadata_change_without_losing_it(tmp_path, monkeypatch):
    directory = record(tmp_path, "editable", {"status": "completed", "task": "Fix the parser crash."})
    original = rundir.write_run_json

    def concurrent(path, data, **kwargs):
        original(path, {"status": "completed", "verdict": "reject", "note": "other writer"})
        return original(path, data, **kwargs)

    monkeypatch.setattr(rundir, "write_run_json", concurrent)
    with pytest.raises(RuntimeError, match="Run changed"):
        RunStore(tmp_path).set_task_type("editable", "docs")
    assert json.loads((directory / "run.json").read_text()) == {
        "status": "completed", "verdict": "reject", "note": "other writer"}


@pytest.mark.parametrize("raw", [b"not JSON", b"[]", b"\xff", b"x" * (1024 * 1024 + 1),
                                  b"[" * 2000 + b"0" + b"]" * 2000],
                         ids=["malformed", "non-object", "invalid-utf8", "oversized", "deeply-nested"])
def test_type_edit_refuses_unsafe_metadata(tmp_path, raw):
    directory = record(tmp_path, "editable", {})
    (directory / "run.json").write_bytes(raw)
    with pytest.raises(RuntimeError):
        RunStore(tmp_path).set_task_type("editable", "docs")
    assert (directory / "run.json").read_bytes() == raw


def test_type_edit_refuses_missing_metadata_and_symlinked_paths(tmp_path):
    store = RunStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.set_task_type("absent", "docs")
    with pytest.raises(ValueError):
        store.set_task_type("../escape", "docs")
    directory = record(tmp_path, "editable", {"status": "completed"})
    (directory / "run.json").unlink()
    with pytest.raises(RuntimeError):
        store.set_task_type("editable", "docs")
    outside = tmp_path / "secret.json"
    outside.write_text('{"status":"completed","task":"private"}')
    (directory / "run.json").symlink_to(outside)
    with pytest.raises(RuntimeError):
        store.set_task_type("editable", "docs")
    assert outside.read_text() == '{"status":"completed","task":"private"}'
    (tmp_path / "alias").symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError):
        store.set_task_type("alias", "docs")


@pytest.fixture
def dashboard_http(tmp_path):
    server = make_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(server, path, *, headers=None, method="GET", body=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, path, headers=headers or {}, body=body)
    response = connection.getresponse()
    status, data = response.status, response.read()
    connection.close()
    return status, json.loads(data)


def task_type_request(server, slug, payload, *, headers=None, raw=False):
    _, snapshot = request(server, "/api/runs")
    defaults = {"Origin": f"http://127.0.0.1:{server.server_port}", "Content-Type": "application/json",
                "X-Dirtywork-CSRF": snapshot["csrf_token"]}
    defaults.update(headers or {})
    defaults = {key: value for key, value in defaults.items() if value is not None}
    return request(server, f"/api/runs/{slug}/task-type", method="POST", headers=defaults,
                   body=payload if raw else json.dumps(payload))


def test_browser_type_edit_and_reset_preserve_verdict(dashboard_http):
    server, root = dashboard_http
    original = {"status": "completed", "task": "Fix the parser crash.", "verdict": "accept",
                "review_seconds": 42, "note": "Reviewed"}
    directory = record(root, "editable", original)
    status, body = task_type_request(server, "editable", {"task_type": "feature"})
    assert status == 200
    assert body["run"]["task_type"]["id"] == "feature"
    assert body["run"]["task_type"]["source"] == "manual"
    assert body["run"]["verdict"] == "accept"
    assert body["run"]["review_seconds"] == 42
    status, body = task_type_request(server, "editable", {"task_type": None})
    assert status == 200
    assert body["run"]["task_type"]["source"] == "suggested"
    assert body["run"]["task_type"]["id"] == "bugfix"
    assert json.loads((directory / "run.json").read_text()) == original


@pytest.mark.parametrize("headers,status", [
    ({"Host": "evil.example"}, 403),
    ({"Origin": "https://evil.example"}, 403),
    ({"Origin": None}, 403),
    ({"X-Dirtywork-CSRF": "wrong"}, 403),
    ({"X-Dirtywork-CSRF": None}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
    ({"Content-Type": "text/plain"}, 415),
    ({"Content-Length": "0"}, 400),
    ({"Content-Length": "nonsense"}, 400),
    ({"Transfer-Encoding": "chunked"}, 400),
])
def test_browser_type_edit_requires_protected_bounded_json(dashboard_http, headers, status):
    server, root = dashboard_http
    directory = record(root, "editable", {"status": "completed"})
    before = (directory / "run.json").read_bytes()
    assert task_type_request(server, "editable", {"task_type": "docs"}, headers=headers)[0] == status
    assert (directory / "run.json").read_bytes() == before


@pytest.mark.parametrize("payload", ["{}", "[]", "null", '{"task_type":"nope"}',
                                    '{"task_type":true}', '{"task_type":[]}', '{"task_type":{}}',
                                    "not json", b"\xff", "x" * 16385,
                                    "[" * 2000 + "0" + "]" * 2000],
                         ids=["missing-type", "array", "null", "invalid-id", "boolean", "type-array",
                              "type-object", "malformed", "invalid-utf8", "oversized", "deeply-nested"])
def test_browser_type_edit_rejects_invalid_payloads(dashboard_http, payload):
    server, root = dashboard_http
    directory = record(root, "editable", {"status": "completed"})
    before = (directory / "run.json").read_bytes()
    assert task_type_request(server, "editable", payload, raw=True)[0] == 400
    assert (directory / "run.json").read_bytes() == before


def test_browser_type_edit_handles_conflict_missing_slug_and_filesystem_errors(dashboard_http, monkeypatch):
    server, root = dashboard_http
    directory = record(root, "editable", {})
    assert task_type_request(server, "missing", {"task_type": "docs"})[0] == 404
    assert task_type_request(server, "%2e%2e%2fescape", {"task_type": "docs"})[0] == 400
    (directory / "run.json").write_text("broken")
    assert task_type_request(server, "editable", {"task_type": "docs"})[0] == 409
    store = server.RequestHandlerClass.keywords["store"]

    def denied(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(store, "set_task_type", denied)
    status, body = task_type_request(server, "editable", {"task_type": "docs"})
    assert status == 503
    assert "error" in body
