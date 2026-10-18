"""Repository scopes retain the same evidence rules as the complete run set."""
import json
from datetime import datetime, timezone

import pytest

from dirtywork.dashboard_data import RunStore


NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def record(root, slug, metadata, events=()):
    directory = root / slug
    directory.mkdir()
    (directory / "run.json").write_text(json.dumps(metadata))
    (directory / "transcript.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events))
    return directory


def test_empty_snapshot_has_no_repository_scopes(tmp_path):
    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    assert snapshot["repositories"] == []
    assert snapshot["runs"] == []
    assert snapshot["summary"]["total"] == 0
    assert snapshot["models"] == []
    assert snapshot["offloading"]["groups"] == []


def test_scopes_use_bounded_literal_recorded_paths_and_sort_unknown_first(tmp_path):
    long_repo = "/recorded/" + "long-path-" * 450
    for slug, metadata in (
        ("west", {"repo": "/west/project"}),
        ("east", {"repo": "/east/project"}),
        ("alias", {"repo": "/east/./project"}),
        ("long", {"repo": long_repo}),
        ("unknown", {}),
        ("malformed", {"repo": {"path": "/west/project"}}),
    ):
        record(tmp_path, slug, {"status": "completed", **metadata})

    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    scopes = snapshot["repositories"]
    assert [scope["repo"] for scope in scopes] == [
        "", "/east/./project", "/east/project", long_repo[:4000], "/west/project"]
    assert [scope["summary"]["total"] for scope in scopes] == [2, 1, 1, 1, 1]
    assert next(run for run in snapshot["runs"] if run["slug"] == "long")["repo"] == long_repo[:4000]
    assert snapshot["summary"]["total"] == 6


def test_repository_summaries_isolate_usage_outcomes_models_and_offloading(tmp_path):
    repo_a, repo_b = "/east/project", "/west/project"
    record(tmp_path, "a-accept", {
        "repo": repo_a, "status": "completed", "model": "shared-model",
        "task": "Fix the parser crash.", "task_type": "bugfix",
        "verdict": "accept", "review_seconds": 4,
    }, [{"event": "run_end", "duration_s": 12,
         "usage": {"prompt_tokens": 100, "completion_tokens": 20}}])
    record(tmp_path, "a-reject", {
        "repo": repo_a, "status": "verify_failed", "model": "east-model",
        "task": "Update the user guide.", "task_type": "docs",
        "verdict": "reject", "review_seconds": 10,
    }, [{"event": "run_end", "duration_s": 40,
         "usage": {"prompt_tokens": 200, "completion_tokens": 30}}])
    record(tmp_path, "a-cleanup", {
        "repo": repo_a, "status": "timeout", "model": "east-model",
        "task": "Configure the linter.", "task_type": "maintenance", "verdict": "cleanup",
    })
    record(tmp_path, "b-accept", {
        "repo": repo_b, "status": "completed", "model": "shared-model",
        "task": "Add CSV exports.", "task_type": "feature",
        "verdict": "accept", "review_seconds": 100,
    }, [{"event": "run_end", "duration_s": 200,
         "usage": {"prompt_tokens": 900, "completion_tokens": 100}}])
    record(tmp_path, "b-pending", {
        "repo": repo_b, "status": "completed", "model": "west-model",
        "task": "Add JSON exports.", "task_type": "feature",
    }, [{"event": "run_end", "duration_s": 60,
         "usage": {"prompt_tokens": 7}}])
    record(tmp_path, "unknown", {"status": "completed", "task": "Investigate whether to upgrade."})

    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    scopes = {scope["repo"]: scope for scope in snapshot["repositories"]}
    assert scopes[repo_a]["summary"] == {
        "total": 3, "active": 0, "reviewing": 0, "completed": 1,
        "reviewed": 2, "accepted": 1, "rejected": 1, "cleanup": 1,
        "awaiting_review": 0, "acceptance_rate": 0.5,
        "median_duration_s": 26, "duration_samples": 2,
        "median_review_s": 7, "review_samples": 2,
        "offloaded_tokens": 350, "offloaded_prompt_tokens": 300,
        "offloaded_completion_tokens": 50, "token_usage_samples": 2,
        "accepted_offloaded_tokens": 120, "accepted_token_usage_samples": 1,
    }
    west = scopes[repo_b]["summary"]
    assert west["total"] == 2
    assert west["awaiting_review"] == 1
    assert west["acceptance_rate"] == 1
    assert west["median_duration_s"] == 130
    assert west["median_review_s"] == 100
    assert west["offloaded_tokens"] == 1000
    assert west["token_usage_samples"] == 1
    assert scopes[""]["summary"]["offloaded_tokens"] is None
    assert scopes[""]["summary"]["acceptance_rate"] is None
    assert scopes[""]["models"][0]["model"] == "Unknown model"
    east_models = scopes[repo_a]["models"]
    assert [model["model"] for model in east_models] == ["east-model", "shared-model"]
    assert east_models[0]["total"] == 2
    assert east_models[0]["acceptance_rate"] == 0
    assert east_models[0]["median_review_s"] == 10
    assert east_models[0]["offloaded_tokens"] == 230
    assert east_models[1]["accepted_offloaded_tokens"] == 120
    assert [model["model"] for model in scopes[repo_b]["models"]] == ["shared-model", "west-model"]
    assert scopes[repo_b]["models"][0]["offloaded_tokens"] == 1000
    east_types = {group["id"]: group for group in scopes[repo_a]["offloading"]["groups"]}
    assert set(east_types) == {"bugfix", "docs", "maintenance"}
    assert east_types["bugfix"]["accepted"] == 1
    assert east_types["docs"]["accepted"] == 0
    assert east_types["docs"]["median_review_s"] == 10
    assert east_types["maintenance"]["reviewed"] == 0
    west_types = scopes[repo_b]["offloading"]["groups"]
    assert len(west_types) == 1
    assert west_types[0]["id"] == "feature"
    assert west_types[0]["total"] == 2
    assert west_types[0]["distinct_tasks"] == 2
    assert west_types[0]["median_review_s"] == 100
    assert scopes[repo_a]["offloading"]["reviewed"] == 2
    assert scopes[repo_b]["offloading"]["reviewed"] == 1
    assert scopes[""]["offloading"]["unclassified"] == 1
    assert snapshot["summary"]["total"] == 6
    assert snapshot["summary"]["acceptance_rate"] == pytest.approx(2 / 3)
    assert snapshot["summary"]["median_review_s"] == 10
    assert snapshot["summary"]["offloaded_tokens"] == 1350
    assert snapshot["offloading"]["reviewed"] == 3
    assert [model["model"] for model in snapshot["models"]] == [
        "Unknown model", "east-model", "shared-model", "west-model"]
    assert next(model for model in snapshot["models"] if model["model"] == "shared-model")["total"] == 2


def test_repository_offloading_uses_full_briefs_before_list_preview_truncation(tmp_path):
    prefix = "Context. " * 8000
    for slug, suffix in (("alpha", "Add alpha support."), ("beta", "Add beta support.")):
        record(tmp_path, slug, {
            "repo": "/real/project", "status": "completed", "task": prefix + suffix,
            "verdict": "accept", "review_seconds": 5,
        })
    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    scope = snapshot["repositories"][0]
    group = scope["offloading"]["groups"][0]
    assert group["id"] == "feature"
    assert group["distinct_tasks"] == 2
    assert group["distinct_reviewed_tasks"] == 2
    assert group["distinct_timed_tasks"] == 2
    assert group["total"] == 2
    assert all(run["task"] == prefix[:240] for run in snapshot["runs"])
    assert "Add alpha support." not in json.dumps(snapshot)
    assert "Add beta support." not in json.dumps(snapshot)


def test_shared_worktree_and_resumed_runs_remain_separate_attempts(tmp_path):
    common = {"repo": "/real/project", "worktree": "/worktrees/shared",
              "branch": "worker/shared", "status": "completed", "task": "Fix the parser crash."}
    record(tmp_path, "initial", {**common, "resumed_by": "follow-up"}, [
        {"event": "run_end", "duration_s": 9,
         "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
    ])
    record(tmp_path, "follow-up", {
        **common, "resumed_from": "initial", "verdict": "accept", "review_seconds": 3,
    }, [{"event": "run_end", "duration_s": 11,
         "usage": {"prompt_tokens": 7, "completion_tokens": 2}}])
    snapshot = RunStore(tmp_path).snapshot(now=NOW)
    scope = snapshot["repositories"][0]
    assert scope["summary"]["total"] == 2
    assert scope["summary"]["reviewed"] == 1
    assert scope["summary"]["awaiting_review"] == 0
    assert scope["summary"]["median_duration_s"] == 10
    assert scope["summary"]["offloaded_tokens"] == 17
    assert scope["offloading"]["groups"][0]["total"] == 2
    assert scope["offloading"]["groups"][0]["distinct_tasks"] == 1
    assert {run["slug"] for run in snapshot["runs"]} == {"initial", "follow-up"}
    assert all(run["worktree"] == "/worktrees/shared" for run in snapshot["runs"])
    assert all(run["branch"] == "worker/shared" for run in snapshot["runs"])


def test_worktree_and_branch_are_safe_literal_metadata_with_start_event_fallback(tmp_path):
    marker = tmp_path / "must-not-exist"
    repo = f"/missing/<script>repo</script>/$(touch {marker})/../project"
    worktree = f"/missing/`touch {marker}`/<script>worktree</script>"
    branch = 'worker/<img src=x onerror="alert(1)">'
    record(tmp_path, "literal", {"status": "completed", "repo": repo,
                                 "worktree": worktree, "branch": branch})
    record(tmp_path, "legacy", {"status": "completed"}, [
        {"event": "run_start", "repo": "/legacy/project", "worktree": "/legacy/worktree",
         "branch": "legacy/branch"},
    ])
    record(tmp_path, "malformed", {"status": "completed", "repo": [], "worktree": {}, "branch": 7})
    store = RunStore(tmp_path)
    detail = store.detail("literal", now=NOW)
    assert (detail["repo"], detail["worktree"], detail["branch"]) == (repo, worktree, branch)
    legacy = store.detail("legacy", now=NOW)
    assert (legacy["repo"], legacy["worktree"], legacy["branch"]) == (
        "/legacy/project", "/legacy/worktree", "legacy/branch")
    snapshot = store.snapshot(now=NOW)
    runs = {run["slug"]: run for run in snapshot["runs"]}
    assert (runs["literal"]["repo"], runs["literal"]["worktree"], runs["literal"]["branch"]) == (
        repo, worktree, branch)
    assert (runs["malformed"]["repo"], runs["malformed"]["worktree"], runs["malformed"]["branch"]) == (
        "", "", "")
    assert repo in [scope["repo"] for scope in snapshot["repositories"]]
    assert not marker.exists()
    json.dumps(snapshot, allow_nan=False)


@pytest.mark.parametrize("field", ["repo", "worktree", "branch"])
def test_repository_metadata_retains_existing_text_bounds(tmp_path, field):
    value = "recorded/" * 600
    record(tmp_path, "bounded", {"status": "completed", field: value})
    store = RunStore(tmp_path)
    assert store.detail("bounded", now=NOW)[field] == value[:4000]
    assert store.snapshot(now=NOW)["runs"][0][field] == value[:4000]
