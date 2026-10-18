"""Dashboard evidence tests: never turn missing data into success or zero."""
import json
import os
from datetime import datetime, timezone

import pytest

from dirtywork.dashboard_data import RunStore


NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def record(root, slug, metadata, events=()):
    path = root / slug
    path.mkdir()
    (path / "run.json").write_text(json.dumps(metadata))
    (path / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events))
    return path


def test_combines_transcript_usage_with_metadata_verdict(tmp_path):
    record(tmp_path, "finished", {
        "status": "completed", "task": "Fix <rounding>", "model": "qwen",
        "started": "2026-10-18T11:58:00Z", "ended": "2026-10-18T12:00:00Z",
        "verdict": "accept", "review_seconds": 42,
    }, [
        {"event": "assistant", "tool_calls": [{"name": "read_file"}]},
        {"event": "tool_result", "tool": "read_file", "args": '{"path":"x.py"}', "result": "hello"},
        {"event": "verify", "passed": True, "exit_code": 0},
        {"event": "run_end", "duration_s": 118, "turns": 1,
         "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
    ])
    store = RunStore(tmp_path)
    run = store.detail("finished", now=NOW)
    assert run["duration_s"] == 118
    assert run["usage"] == {"prompt_tokens": 100, "completion_tokens": 20}
    assert run["tool_calls"] == 1
    assert run["verify"]["passed"] is True
    assert run["task"] == "Fix <rounding>"
    assert run["verdict"] == "accept"
    assert run["activity"][-1]["event"] == "run_end"
    assert run["activity"][1]["target"] == "x.py"


def test_outcome_denominators_exclude_cleanup_and_missing_metrics(tmp_path):
    record(tmp_path, "accept", {"status": "completed", "model": "a", "verdict": "accept",
                              "review_seconds": 0}, [{"event": "run_end", "duration_s": 10}])
    record(tmp_path, "reject", {"status": "verify_failed", "model": "a", "verdict": "reject"},
           [{"event": "run_end", "duration_s": 30}])
    record(tmp_path, "cleanup", {"status": "timeout", "verdict": "cleanup"})
    record(tmp_path, "pending", {"status": "completed", "model": "b"})
    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    summary = snapshot["summary"]
    assert summary["total"] == 4
    assert summary["accepted"] == 1
    assert summary["reviewed"] == 2
    assert summary["acceptance_rate"] == 0.5
    assert summary["cleanup"] == 1
    assert summary["awaiting_review"] == 1
    assert summary["median_duration_s"] == 20
    assert summary["duration_samples"] == 2
    assert summary["median_review_s"] == 0
    assert summary["review_samples"] == 1
    model = next(m for m in snapshot["models"] if m["model"] == "b")
    assert model["acceptance_rate"] is None
    assert model["median_duration_s"] is None


def test_partial_transcript_recovers_on_append_and_metadata_refreshes(tmp_path):
    path = record(tmp_path, "live", {"status": "running", "host_pid": os.getpid(),
                                     "started": "2026-10-18T11:59:00Z"},
                  [{"event": "assistant"}])
    transcript = path / "transcript.jsonl"
    with transcript.open("a") as f:
        f.write('{"event":"tool_result","tool":"bash"')
    store = RunStore(tmp_path)
    run = store.detail("live", now=NOW)
    assert run["activity_state"] == "running"
    assert run["turns"] == 1
    assert run["tool_calls"] == 0
    assert run["duration_s"] == 60
    with transcript.open("a") as f:
        f.write(',"result":"ok"}\n')
    assert store.detail("live", now=NOW)["tool_calls"] == 1
    (path / "run.json").write_text(json.dumps({"status": "completed", "verdict": "reject"}))
    assert store.detail("live", now=NOW)["verdict"] == "reject"


def test_inactive_and_unconfirmed_are_not_counted_as_live(tmp_path):
    record(tmp_path, "dead", {"status": "running", "host_pid": -1})
    record(tmp_path, "unknown", {"status": "running"})
    store = RunStore(tmp_path)
    assert store.detail("dead", now=NOW)["activity_state"] == "unconfirmed"
    assert store.detail("unknown", now=NOW)["activity_state"] == "unconfirmed"
    assert store.snapshot(now=NOW)["summary"]["active"] == 0


def test_corrupt_records_do_not_hide_other_runs_and_nonfinite_values_are_unknown(tmp_path):
    bad = record(tmp_path, "bad", {})
    (bad / "run.json").write_text("not json")
    path = record(tmp_path, "good", {"status": "completed", "review_seconds": float("nan")},
                  [{"event": "future_event"}, {"event": "run_end", "duration_s": float("inf")}])
    with (path / "transcript.jsonl").open("a") as f:
        f.write("[]\ninvalid\n")
    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    assert len(snapshot["runs"]) == 2
    assert snapshot["summary"]["median_duration_s"] is None
    assert snapshot["summary"]["median_review_s"] is None
    assert snapshot["warnings"]
    json.dumps(snapshot, allow_nan=False)


def test_missing_root_empty_and_traversal_or_symlink_refused(tmp_path):
    assert RunStore(tmp_path / "absent").snapshot(now=NOW)["runs"] == []
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "runs"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    store = RunStore(root)
    for slug in ("../outside", "link", "missing"):
        with pytest.raises((ValueError, FileNotFoundError)):
            store.detail(slug, now=NOW)
    assert store.snapshot(now=NOW)["runs"] == []


@pytest.fixture
def dashboard_http(tmp_path):
    import threading
    from dirtywork.dashboard import make_server
    server = make_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(server, path, *, headers=None, method="GET", body=None):
    import http.client
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, path, headers=headers or {}, body=body)
    response = connection.getresponse()
    body = response.read()
    status, headers = response.status, dict(response.getheaders())
    connection.close()
    return status, headers, body


def test_http_assets_and_api_show_new_runs_without_restart(dashboard_http):
    server, root = dashboard_http
    status, headers, body = request(server, "/")
    assert status == 200
    assert b"<title>dirtywork" in body
    assert headers["Cache-Control"] == "no-store"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    for path, mime in (("/app.js", "javascript"), ("/wopr.js", "javascript"),
                       ("/presentation.js", "javascript"), ("/style.css", "text/css")):
        status, headers, body = request(server, path)
        assert status == 200 and mime in headers["Content-Type"] and body
    assert json.loads(request(server, "/api/runs")[2])["runs"] == []
    record(root, "new", {"task": "Real task", "status": "completed"})
    assert json.loads(request(server, "/api/runs")[2])["runs"][0]["task"] == "Real task"
    assert json.loads(request(server, "/api/runs/new")[2])["slug"] == "new"


@pytest.mark.parametrize("path,headers,method,status", [
    ("/api/runs", {"Host": "evil.example"}, "GET", 403),
    ("/api/runs", {"Origin": "https://evil.example"}, "GET", 403),
    ("/api/runs", {"Sec-Fetch-Site": "cross-site"}, "GET", 403),
    ("/api/runs", {}, "POST", 405),
    ("/api/runs/%2e%2e%2fsecret", {}, "GET", 400),
    ("/api/runs/not-here", {}, "GET", 404),
    ("/../pyproject.toml", {}, "GET", 404),
    ("/run.json", {}, "GET", 404),
])
def test_http_refuses_external_requests_writes_and_arbitrary_files(dashboard_http, path, headers, method, status):
    assert request(dashboard_http[0], path, headers=headers, method=method)[0] == status


def test_symlinked_metadata_and_transcripts_are_not_exposed(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text('{"task":"TOP SECRET"}')
    path = record(tmp_path, "link-files", {})
    for name in ("run.json", "transcript.jsonl"):
        (path / name).unlink()
        (path / name).symlink_to(secret)
    run = RunStore(tmp_path).detail("link-files", now=NOW)
    assert run["task"] == ""
    assert len(run["warnings"]) == 2


def test_dashboard_cli_accepts_local_options_and_rejects_invalid_port(tmp_path):
    from dirtywork.__main__ import _parse_args, main
    args = _parse_args(["dashboard", "--port", "0", "--runs-dir", str(tmp_path), "--open"])
    assert args.port == 0 and args.runs_dir == tmp_path and args.open
    assert main(["dashboard", "--port", "65536"]) == 2


def test_start_event_fills_legacy_metadata_and_terminal_evidence_wins_running_label(tmp_path):
    task = "Long task " + "a" * 5000
    record(tmp_path, "legacy", {"status": "running"}, [
        {"event": "run_start", "ts": "2026-10-18T11:59:00Z", "model": "legacy-model", "task": task},
        {"event": "run_end", "ts": "2026-10-18T12:00:00Z", "status": "completed"},
    ])
    store = RunStore(tmp_path)
    run = store.detail("legacy", now=NOW)
    assert run["model"] == "legacy-model"
    assert run["task"] == task
    assert run["status"] == "completed"
    assert run["duration_s"] == 60


def test_internal_directory_symlink_refused_even_with_valid_target(tmp_path):
    record(tmp_path, "real", {"task": "Internal record"})
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(ValueError):
        RunStore(tmp_path).detail("alias", now=NOW)


def test_summary_omits_large_detail_evidence_and_prunes_deleted_cache(tmp_path):
    import shutil
    path = record(tmp_path, "one", {"task": "a" * 5000, "files_changed": ["x.py"]})
    store = RunStore(tmp_path)
    run = store.snapshot(now=NOW)["runs"][0]
    assert len(run["task"]) <= 240
    assert "files_changed" not in run
    assert set(store._transcripts) == {"one"}
    shutil.rmtree(path)
    assert store.snapshot(now=NOW)["runs"] == []
    assert store._transcripts == {}


def test_out_of_range_numbers_and_deep_json_do_not_disconnect_dashboard(tmp_path):
    record(tmp_path, "numeric", {"status": "running", "host_pid": 10 ** 100,
                                "review_seconds": 10 ** 400}, [
        {"event": "run_end", "duration_s": 10 ** 400,
         "usage": {"prompt_tokens": 10 ** 400}},
    ])
    path = record(tmp_path, "nested", {})
    (path / "run.json").write_text('[' * 2000 + '0' + ']' * 2000)
    (path / "transcript.jsonl").write_text('[' * 2000 + '0' + ']' * 2000 + '\n')
    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    assert snapshot["summary"]["total"] == 2
    assert snapshot["summary"]["median_duration_s"] is None
    assert snapshot["warnings"]
    json.dumps(snapshot, allow_nan=False)


def test_out_of_range_pid_is_unconfirmed_not_an_exception(tmp_path):
    record(tmp_path, "numeric-pid", {"status": "running", "host_pid": 10 ** 100})
    assert RunStore(tmp_path).detail("numeric-pid", now=NOW)["activity_state"] == "unconfirmed"


def test_rolling_activity_preserves_retained_event_identity(tmp_path):
    path = record(tmp_path, "tail", {"status": "completed"}, [
        {"event": "tool_result", "tool": "read_file", "result": str(i)} for i in range(65)
    ])
    store = RunStore(tmp_path)
    before = store.detail("tail", now=NOW)["activity"]
    with (path / "transcript.jsonl").open("a") as f:
        f.write(json.dumps({"event": "tool_result", "tool": "read_file", "result": "next"}) + '\n')
    after = store.detail("tail", now=NOW)["activity"]
    assert before[1]["sequence"] == after[0]["sequence"]
    assert len(before) == len(after) == 60
    assert len({e["sequence"] for e in after}) == 60


def test_timeline_spans_full_run_and_refreshes_with_recorded_review(tmp_path):
    path = record(tmp_path, "timeline", {"status": "running", "host_pid": os.getpid(),
                   "started": "2026-10-18T11:59:00Z"}, [
        {"event": "assistant", "ts": "2026-10-18T11:59:02Z"},
        *[{"event": "tool_result", "tool": "read_file", "ts": "2026-10-18T11:59:10Z"} for _ in range(65)],
        {"event": "verify", "ts": "2026-10-18T11:59:30Z", "passed": False},
        {"event": "nudge", "ts": "bad timestamp", "kind": "stall"},
    ])
    store = RunStore(tmp_path)
    timeline = store.detail("timeline", now=NOW)["timeline"]
    assert timeline["points"][0]["lane"] == "model"
    assert timeline["points"][0]["at_s"] == 2
    assert len([p for p in timeline["points"] if p["lane"] == "tools"]) == 65
    assert timeline["elapsed_s"] == 60
    assert timeline["untimed_events"] == 1
    assert timeline["points"][-1]["outcome"] == "failed"
    (path / "run.json").write_text(json.dumps({"status": "completed",
        "started": "2026-10-18T11:59:00Z", "ended": "2026-10-18T11:59:40Z",
        "verdict": "accept", "verdict_at": "2026-10-18T12:00:00Z"}))
    updated = store.detail("timeline", now=NOW)["timeline"]
    assert updated["points"][-1]["lane"] == "review"
    assert updated["points"][-1]["at_s"] == 60
    assert updated["elapsed_s"] == 40


def test_timeline_does_not_invent_missing_times_or_review_timestamp(tmp_path):
    record(tmp_path, "untimed", {"status": "completed", "verdict": "accept"},
           [{"event": "assistant"}, {"event": "tool_result", "ts": "invalid"}])
    timeline = RunStore(tmp_path).detail("untimed", now=NOW)["timeline"]
    assert timeline["points"] == []
    assert timeline["untimed_events"] == 3
    assert timeline["elapsed_s"] is None


def test_timeline_is_bounded_without_resetting_recorded_time_origin(tmp_path):
    record(tmp_path, "long", {"started": "2026-10-18T11:59:00Z", "status": "completed"}, [
        {"event": "tool_result", "ts": "2026-10-18T11:59:10Z", "tool": "read_file"} for _ in range(2005)
    ])
    timeline = RunStore(tmp_path).detail("long", now=NOW)["timeline"]
    assert len(timeline["points"]) == 2000
    assert timeline["omitted_events"] == 5
    assert timeline["points"][0]["at_s"] == 10
    assert timeline["points"][0]["sequence"] == 5


def test_offloaded_tokens_sum_complete_usage_and_separate_accepted_work(tmp_path):
    record(tmp_path, "accepted", {"status": "completed", "verdict": "accept"}, [
        {"event": "run_end", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
    ])
    record(tmp_path, "rejected", {"status": "completed", "verdict": "reject"}, [
        {"event": "run_end", "usage": {"prompt_tokens": 200, "completion_tokens": 20}},
    ])
    record(tmp_path, "missing", {"status": "completed", "verdict": "accept"})
    record(tmp_path, "partial", {"status": "completed"}, [
        {"event": "run_end", "usage": {"prompt_tokens": 900}},
    ])
    record(tmp_path, "active", {"status": "running", "host_pid": os.getpid()})
    summary = RunStore(tmp_path).snapshot(now=NOW)["summary"]
    assert summary["offloaded_tokens"] == 1320
    assert summary["offloaded_prompt_tokens"] == 1200
    assert summary["offloaded_completion_tokens"] == 120
    assert summary["token_usage_samples"] == 2
    assert summary["accepted_offloaded_tokens"] == 1100
    assert summary["accepted_token_usage_samples"] == 1


def test_offloaded_tokens_distinguish_no_usage_from_recorded_zero(tmp_path):
    assert RunStore(tmp_path).snapshot(now=NOW)["summary"]["offloaded_tokens"] is None
    record(tmp_path, "zero", {"status": "completed"}, [
        {"event": "run_end", "usage": {"prompt_tokens": 0, "completion_tokens": 0}},
    ])
    summary = RunStore(tmp_path).snapshot(now=NOW)["summary"]
    assert summary["offloaded_tokens"] == 0
    assert summary["token_usage_samples"] == 1
    assert summary["accepted_offloaded_tokens"] is None


def review_request(server, slug, action, *, note="", headers=None):
    snapshot = json.loads(request(server, "/api/runs")[2])
    defaults = {"Origin": f"http://127.0.0.1:{server.server_port}",
                "Content-Type": "application/json", "X-Dirtywork-CSRF": snapshot["csrf_token"]}
    defaults.update(headers or {})
    return request(server, f"/api/runs/{slug}/review", method="POST", headers=defaults,
                   body=json.dumps({"action": action, "note": note}))


def test_browser_review_starts_timer_records_note_and_preserves_retry(dashboard_http):
    server, root = dashboard_http
    path = record(root, "reviewable", {"status": "completed"})
    assert review_request(server, "reviewable", "start")[0] == 200
    start = json.loads((path / "run.json").read_text())["review_started_at"]
    assert review_request(server, "reviewable", "start")[0] == 200
    assert json.loads((path / "run.json").read_text())["review_started_at"] == start
    status, _, body = review_request(server, "reviewable", "accept", note="Verified the diff")
    assert status == 200
    run = json.loads(body)["run"]
    assert run["verdict"] == "accept" and run["review_seconds"] >= 0
    saved = (path / "run.json").read_bytes()
    assert json.loads(saved)["note"] == "Verified the diff"
    assert review_request(server, "reviewable", "accept")[0] == 200
    assert (path / "run.json").read_bytes() == saved
    assert review_request(server, "reviewable", "reject")[0] == 409
    assert (path / "run.json").read_bytes() == saved


def test_browser_cannot_record_untimed_review_or_review_running_run(dashboard_http):
    server, root = dashboard_http
    path = record(root, "finished", {"status": "completed"})
    before = (path / "run.json").read_bytes()
    assert review_request(server, "finished", "reject")[0] == 409
    assert (path / "run.json").read_bytes() == before
    record(root, "running", {"status": "running"})
    assert review_request(server, "running", "start")[0] == 409


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"},
    {"X-Dirtywork-CSRF": "wrong"}, {"Content-Type": "text/plain"},
    {"Sec-Fetch-Site": "cross-site"}])
def test_browser_review_refuses_foreign_or_unprotected_writes(dashboard_http, headers):
    server, root = dashboard_http
    path = record(root, "safe", {"status": "completed"})
    before = (path / "run.json").read_bytes()
    assert review_request(server, "safe", "start", headers=headers)[0] in (403, 415)
    assert (path / "run.json").read_bytes() == before


def test_browser_review_validates_note_slug_and_symlink(dashboard_http, tmp_path):
    server, root = dashboard_http
    path = record(root, "safe", {"status": "completed"})
    assert review_request(server, "safe", "delete")[0] == 400
    assert review_request(server, "safe", "start", note="x" * 2001)[0] == 400
    assert review_request(server, "missing", "start")[0] == 404
    assert review_request(server, "%2e%2e%2fescape", "start")[0] == 400
    secret = tmp_path / "secret.json"
    secret.write_text('{"status":"completed"}')
    (path / "run.json").unlink()
    (path / "run.json").symlink_to(secret)
    assert review_request(server, "safe", "start")[0] == 409
    assert secret.read_text() == '{"status":"completed"}'


def test_browser_review_rejects_internal_directory_alias_before_writing(dashboard_http):
    server, root = dashboard_http
    path = record(root, "real", {"status": "completed"})
    (root / "alias").symlink_to(path, target_is_directory=True)
    before = (path / "run.json").read_bytes()
    assert review_request(server, "alias", "start")[0] == 400
    assert (path / "run.json").read_bytes() == before


def test_changes_reads_saved_patch_and_refreshes(tmp_path):
    directory = record(tmp_path, 'demo', {'status': 'completed'})
    patch = directory / 'diff.patch'
    patch.write_text('diff --git a/demo.txt b/demo.txt\nnew file mode 100644\n--- /dev/null\n+++ b/demo.txt\n@@ -0,0 +1 @@\n+hello\n')
    store = RunStore(tmp_path)
    result = store.changes('demo')
    assert result['files'][0]['path'] == 'demo.txt'
    assert '+hello' in result['files'][0]['diff']
    patch.write_text('diff --git a/demo.txt b/demo.txt\n--- a/demo.txt\n+++ b/demo.txt\n@@ -1 +1 @@\n-hello\n+updated\n')
    assert '+updated' in store.changes('demo')['files'][0]['diff']


def live_capture(directory, patch, **overrides):
    capture = {'version': 1, 'state': 'ready', 'patch': patch,
               'captured_at': '2026-10-18T12:00:00+00:00', 'message': ''}
    capture.update(overrides)
    (directory / 'live_changes.json').write_text(json.dumps(capture))


def test_active_changes_refresh_live_patch_and_include_new_files(tmp_path):
    directory = record(tmp_path, 'active', {'status': 'running', 'host_pid': os.getpid()})
    patch = 'diff --git a/existing.txt b/existing.txt\n--- a/existing.txt\n+++ b/existing.txt\n@@ -1 +1 @@\n-old\n+first\n'
    live_capture(directory, patch)
    store = RunStore(tmp_path)
    first = store.changes('active')
    assert first['source'] == 'live'
    assert first['live'] is True
    assert first['captured_at'] == '2026-10-18T12:00:00+00:00'
    assert first['files'][0]['rows'][-1]['text'] == 'first'
    assert first['files'][0]['additions'] == 1
    patch = patch.replace('+first', '+updated') + 'diff --git a/new.txt b/new.txt\nnew file mode 100644\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+new file\n'
    live_capture(directory, patch, captured_at='2026-10-18T12:00:03+00:00')
    updated = store.changes('active')
    assert updated['files'][0]['rows'][-1]['text'] == 'updated'
    assert updated['files'][1]['path'] == 'new.txt'
    assert updated['files'][1]['change_type'] == 'New file'
    assert updated['captured_at'] == '2026-10-18T12:00:03+00:00'


def test_active_resume_prefers_live_capture_to_previous_saved_export(tmp_path):
    directory = record(tmp_path, 'active', {'status': 'running', 'host_pid': os.getpid()})
    (directory / 'diff.patch').write_text('diff --git a/old b/old\n--- /dev/null\n+++ b/old\n@@ -0,0 +1 @@\n+stale export\n')
    store = RunStore(tmp_path)
    assert store.changes('active')['files'] == []
    live_capture(directory, 'diff --git a/current b/current\n--- /dev/null\n+++ b/current\n@@ -0,0 +1 @@\n+current work\n')
    result = store.changes('active')
    assert [file['path'] for file in result['files']] == ['current']
    assert result['live'] is True


def test_completed_changes_prefer_saved_export_and_fallback_to_last_live_capture(tmp_path):
    directory = record(tmp_path, 'done', {'status': 'completed'})
    live_capture(directory, 'diff --git a/live b/live\n--- /dev/null\n+++ b/live\n@@ -0,0 +1 @@\n+captured\n')
    store = RunStore(tmp_path)
    fallback = store.changes('done')
    assert fallback['files'][0]['path'] == 'live'
    assert fallback['source'] == 'live'
    assert fallback['live'] is False
    assert 'Last live snapshot' in fallback['message']
    (directory / 'diff.patch').write_text('diff --git a/saved b/saved\n--- /dev/null\n+++ b/saved\n@@ -0,0 +1 @@\n+final export\n')
    result = store.changes('done')
    assert result['files'][0]['path'] == 'saved'
    assert result['source'] == 'saved'
    assert result['live'] is False
    assert result.get('captured_at') is None


@pytest.mark.parametrize('metadata', [
    {'status': 'running'},
    {'status': 'running', 'host_pid': -1},
    {'status': 'running', 'host_pid': os.getpid()},
])
def test_only_genuinely_active_run_labels_capture_live(tmp_path, metadata):
    directory = record(tmp_path, 'demo', metadata)
    live_capture(directory, '')
    result = RunStore(tmp_path).changes('demo')
    assert result['live'] is (metadata.get('host_pid') == os.getpid())
    if not result['live']:
        assert 'Last live snapshot' in result['message']


@pytest.mark.parametrize('content', [
    b'not json', b'[]', b'\xff',
    json.dumps({'version': True, 'state': 'ready', 'patch': '', 'captured_at': '2026-10-18T12:00:00Z', 'message': ''}).encode(),
    json.dumps({'version': 1, 'state': 'unknown', 'patch': '', 'captured_at': '2026-10-18T12:00:00Z', 'message': ''}).encode(),
    json.dumps({'version': 1, 'state': 'ready', 'patch': [], 'captured_at': '2026-10-18T12:00:00Z', 'message': ''}).encode(),
    json.dumps({'version': 1, 'state': 'ready', 'patch': '', 'captured_at': 'not a timestamp', 'message': ''}).encode(),
    json.dumps({'version': 1, 'state': 'ready', 'patch': '', 'captured_at': '2026-10-18T12:00:00', 'message': ''}).encode(),
    json.dumps({'version': 1, 'state': 'ready', 'patch': '\ud800', 'captured_at': '2026-10-18T12:00:00Z', 'message': ''}).encode(),
])
def test_invalid_live_capture_is_explicitly_unavailable(tmp_path, content):
    directory = record(tmp_path, 'active', {'status': 'running', 'host_pid': os.getpid()})
    (directory / 'live_changes.json').write_bytes(content)
    result = RunStore(tmp_path).changes('active')
    assert result['files'] == []
    assert 'unavailable' in result['message'].lower()


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'large_record', 'large_patch'])
def test_live_capture_refuses_unsafe_files_and_bounds_record_and_patch(tmp_path, kind):
    directory = record(tmp_path, 'active', {'status': 'running', 'host_pid': os.getpid()})
    target = directory / 'live_changes.json'
    if kind == 'symlink':
        outside = record(tmp_path, 'outside', {'status': 'completed'})
        live_capture(outside, 'secret')
        target.symlink_to(outside / 'live_changes.json')
    elif kind == 'fifo':
        os.mkfifo(target)
    elif kind == 'large_record':
        target.write_bytes(b' ' * (4 * 1024 * 1024 + 1))
    else:
        live_capture(directory, '\u00e9' * (512 * 1024 + 1))
    result = RunStore(tmp_path).changes('active')
    assert result['files'] == []
    assert 'unavailable' in result['message'].lower() or 'limit' in result['message'].lower()
    assert RunStore(tmp_path).snapshot()['runs']


@pytest.mark.parametrize('state,message', [('too_large', 'Changes exceed preview limit.'),
                                          ('unavailable', 'Capture could not inspect the worktree.')])
def test_live_capture_state_explains_why_preview_is_missing(tmp_path, state, message):
    directory = record(tmp_path, 'active', {'status': 'running', 'host_pid': os.getpid()})
    live_capture(directory, '', state=state, message=message)
    result = RunStore(tmp_path).changes('active')
    assert result['files'] == []
    assert message in result['message']


def test_browser_changes_coalesce_refreshes_and_preserve_reading_context():
    """Exercise the real loader with deferred HTTP responses and a small DOM."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for the dashboard browser regression')
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  function diffCode(');
const loader = source.slice(start, source.indexOf("  $('files-details').addEventListener", start));
class Element {
  constructor(tag, className, text) { this.tag = tag; this.className = className; this.textContent = text || ''; this.dataset = {}; this.children = []; this.open = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute() {}
  querySelectorAll() { return this.children.filter(node => node.tag === 'details' && node.open); }
}
function browser() {
  const state = {selected: 'alpha', detailRevision: 0};
  const nodes = {'files-output': new Element('div'), 'files-status': new Element('p'), 'files-details': new Element('details')};
  nodes['files-details'].open = true;
  const requests = [];
  const fetch = path => new Promise(resolve => requests.push({path, finish: data => resolve({ok: true, json: async () => data})}));
  const context = {state, fetch, api: async path => (await fetch(path)).json(),
    $: id => nodes[id], set: (id, value) => { nodes[id].textContent = value; },
    element: (tag, className, text) => new Element(tag, className, text)};
  vm.createContext(context);
  vm.runInContext(loader + '\nthis.loadChanges = loadChanges;', context);
  return {...context, nodes, requests};
}
function data(text = 'first', captured_at = '2026-10-18T12:00:00Z') {
  return {message: 'Live file changes · updates after worker actions.', live: true, source: 'live', captured_at,
    files: ['one.txt', 'two.txt'].map(path => ({path, diff: '+' + text, change_type: 'Modified', additions: 1, deletions: 0,
      rows: [{kind: 'add', text, old: null, new: 1}]}))};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const view = browser();
  const first = view.loadChanges('alpha');
  const repeated = view.loadChanges('alpha');
  assert.equal(view.requests.length, 1, 'refreshes must share the pending changes request');
  view.requests[0].finish(data());
  await first; await repeated;
  const row = view.nodes['files-output'].children[0];
  row.open = true;
  const selectedTextNode = row.children[1].children[0].children[3];
  const unchanged = view.loadChanges('alpha');
  view.requests[1].finish(data('first', '2026-10-18T12:00:03Z'));
  await unchanged;
  assert.equal(view.nodes['files-output'].children[0], row, 'capture timestamps must not replace the file DOM');
  assert.equal(view.nodes['files-output'].children[0].children[1].children[0].children[3], selectedTextNode, 'selected text stays attached to the same DOM node');
  const updated = view.loadChanges('alpha');
  view.requests[2].finish(data('updated'));
  await updated;
  assert.equal(view.nodes['files-output'].children[0].open, true, 'updated diffs retain expanded files');
  assert.equal(view.nodes['files-output'].children[0].children[1].children[0].children[3].textContent, 'updated');

  const switched = browser();
  const stale = switched.loadChanges('alpha');
  switched.state.selected = 'beta'; switched.state.detailRevision++;
  switched.loadChanges('beta');
  switched.state.selected = 'alpha'; switched.state.detailRevision++;
  switched.loadChanges('alpha');
  assert.equal(switched.requests.length, 1, 'selection refreshes coalesce while a request is pending');
  switched.requests[0].finish(data('stale selection'));
  await stale; await settle();
  assert.equal(switched.nodes['files-output'].children.length, 0, 'an old response cannot overwrite a reselected run');
  assert.equal(switched.requests.length, 2, 'the current selection is fetched after the pending request settles');
  assert.match(switched.requests[1].path, /\/alpha\/changes$/);
  switched.requests[1].finish(data('fresh selection'));
  await settle();
  assert.equal(switched.nodes['files-output'].children[0].children[1].children[0].children[3].textContent, 'fresh selection');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    app = Path(__file__).parents[1] / 'dirtywork' / 'dashboard_assets' / 'app.js'
    result = subprocess.run([node, '-e', script, str(app)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_changes_refuses_symlinks_and_bounds_patch(tmp_path):
    directory = record(tmp_path, 'demo', {'status': 'running'})
    assert RunStore(tmp_path).changes('demo')['files'] == []
    outside = tmp_path / 'outside'
    outside.write_text('secret')
    (directory / 'diff.patch').symlink_to(outside)
    assert RunStore(tmp_path).changes('demo')['files'] == []
    (directory / 'diff.patch').unlink()
    (directory / 'diff.patch').write_bytes(b'x' * (1024 * 1024 + 1))
    assert RunStore(tmp_path).changes('demo')['files'] == []
    with pytest.raises(ValueError):
        RunStore(tmp_path).changes('../outside')


def test_changes_endpoint(dashboard_http):
    server, root = dashboard_http
    directory = record(root, 'demo', {'status': 'completed'})
    (directory / 'diff.patch').write_text('diff --git a/a b/a\n--- /dev/null\n+++ b/a\n@@ -0,0 +1 @@\n+safe <text>\n')
    status, _, body = request(server, '/api/runs/demo/changes')
    assert status == 200
    assert json.loads(body)['files'][0]['path'] == 'a'
    assert request(server, '/api/runs/missing/changes')[0] == 404
    assert request(server, '/api/runs/demo/changes', headers={'Origin': 'https://evil.example'})[0] == 403


def test_diff_rows_hide_metadata_and_track_both_line_numbers(tmp_path):
    directory = record(tmp_path, 'demo', {'status': 'completed'})
    (directory / 'diff.patch').write_text('diff --git a/a b/a\nindex abc..def 100644\n--- a/a\n+++ b/a\n@@ -4,2 +4,3 @@ example\n same\n-old\n+new\n+extra\n\\ No newline at end of file\n')
    file = RunStore(tmp_path).changes('demo')['files'][0]
    assert file['additions'] == 2
    assert file['deletions'] == 1
    assert file['rows'] == [
        {'kind': 'hunk', 'text': 'example', 'old': None, 'new': None},
        {'kind': 'context', 'text': 'same', 'old': 4, 'new': 4},
        {'kind': 'remove', 'text': 'old', 'old': 5, 'new': None},
        {'kind': 'add', 'text': 'new', 'old': None, 'new': 5},
        {'kind': 'add', 'text': 'extra', 'old': None, 'new': 6},
        {'kind': 'notice', 'text': 'No newline at end of file', 'old': None, 'new': None},
    ]


@pytest.mark.parametrize('via_cli', [False, True])
def test_review_does_not_follow_planted_temporary_hardlink(tmp_path, monkeypatch, via_cli):
    from dirtywork import rundir
    directory = record(tmp_path, 'demo', {'status': 'completed'})
    outside = tmp_path / 'unrelated.txt'
    outside.write_text('keep this')
    os.link(outside, directory / '.run.json.collision.tmp')
    tokens = iter(['collision', 'fresh'])
    monkeypatch.setattr(rundir.secrets, 'token_hex', lambda *a: next(tokens))
    if via_cli:
        from dirtywork.__main__ import main
        monkeypatch.setattr(rundir, 'RUNS_DIR', tmp_path)
        assert main(['runs', 'review-start', 'demo']) == 0
    else:
        RunStore(tmp_path).review_action('demo', 'start')
    assert outside.read_text() == 'keep this'


def test_superseded_review_is_closed_and_not_awaiting(tmp_path):
    record(tmp_path, 'old', {'status': 'completed', 'resumed_by': 'new',
           'review_started_at': '2026-10-18T10:00:00Z'})
    store = RunStore(tmp_path)
    run = store.detail('old', now=NOW)
    assert not run['review_in_progress']
    assert run['review_elapsed_s'] is None
    assert store.snapshot(now=NOW)['summary']['awaiting_review'] == 0


def test_late_review_keeps_worker_axis(tmp_path):
    record(tmp_path, 'late', {'status': 'completed', 'started': '2026-10-18T10:00:00Z',
           'ended': '2026-10-18T10:02:00Z', 'review_started_at': '2026-10-18T11:59:00Z'},
           [{'event': 'assistant', 'ts': '2026-10-18T10:01:00Z'}])
    timeline = RunStore(tmp_path).detail('late', now=NOW)['timeline']
    assert timeline['elapsed_s'] == 120
    assert timeline['points'][0]['at_s'] == 60
    assert timeline['points'][-1]['at_s'] == 7140


def test_unsearchable_record_does_not_hide_readable_runs(tmp_path, monkeypatch):
    from pathlib import Path
    record(tmp_path, 'good', {'status': 'completed'})
    bad = record(tmp_path, 'bad', {'status': 'completed'})
    original = Path.is_dir
    def denied(path):
        if path == bad:
            raise PermissionError('denied')
        return original(path)
    monkeypatch.setattr(Path, 'is_dir', denied)
    result = RunStore(tmp_path).snapshot(now=NOW)
    assert [r['slug'] for r in result['runs']] == ['good']
    assert any('bad' in warning for warning in result['warnings'])


@pytest.mark.parametrize('path', ['/api/runs', '/api/runs/demo', '/api/runs/demo/changes'])
def test_http_filesystem_errors_are_json(dashboard_http, monkeypatch, path):
    server, _ = dashboard_http
    store = server.RequestHandlerClass.keywords['store']
    def denied(*args, **kwargs):
        raise PermissionError('denied')
    for name in ('snapshot', 'detail', 'changes'):
        monkeypatch.setattr(store, name, denied)
    status, _, body = request(server, path)
    assert status == 503
    assert 'error' in json.loads(body)


def test_active_host_changes_wait_for_next_capture(tmp_path):
    record(tmp_path, 'host', {'status': 'running', 'sandbox': 'none', 'host_pid': os.getpid()})
    result = RunStore(tmp_path).changes('host')
    assert 'next capture' in result['message']
    assert 'do not export' not in result['message']


def test_ended_host_without_artifacts_explains_missing_export(tmp_path):
    record(tmp_path, 'host', {'status': 'completed', 'sandbox': 'none'})
    result = RunStore(tmp_path).changes('host')
    assert 'Host-mode' in result['message']
    assert 'worktree' in result['message']


@pytest.mark.parametrize('metadata,events', [
    ({'status': 'running', 'host_pid': os.getpid()}, [{'event': 'run_end', 'status': 'completed'}]),
    ({'status': 'running', 'host_pid': 2147483647}, []),
])
def test_projected_review_eligibility_matches_browser_action(tmp_path, monkeypatch, metadata, events):
    import dirtywork.review as review
    monkeypatch.setattr(review, 'pid_alive', lambda pid: pid == os.getpid())
    record(tmp_path, 'eligible', metadata, events)
    store = RunStore(tmp_path)
    assert store.detail('eligible')['review_eligible']
    assert store.review_action('eligible', 'start')['review_in_progress']


def test_browser_save_detects_concurrent_metadata_change(tmp_path, monkeypatch):
    from dirtywork import rundir
    directory = record(tmp_path, 'demo', {'status': 'completed'})
    original = rundir.write_run_json
    def concurrent(path, data, **kwargs):
        original(path, {'status': 'completed', 'note': 'other writer'})
        return original(path, data, **kwargs)
    monkeypatch.setattr(rundir, 'write_run_json', concurrent)
    with pytest.raises(RuntimeError, match='Run changed'):
        RunStore(tmp_path).review_action('demo', 'start')
    assert json.loads((directory / 'run.json').read_text())['note'] == 'other writer'


def test_bundled_wopr_font_is_served(dashboard_http):
    status, headers, body = request(dashboard_http[0], '/VT323-Regular.ttf')
    assert status == 200
    assert headers['Content-Type'] == 'font/ttf'
    assert body[:4] == b'\x00\x01\x00\x00'
