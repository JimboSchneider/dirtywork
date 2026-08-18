# tests/test_docker_args.py
from __future__ import annotations

from pathlib import Path

from dirtywork.sandbox.docker_args import (
    DEFAULT_IMAGE,
    PATH_ENV,
    PINNED_DIGEST,
    DockerConfig,
    container_name,
    exec_argv,
    export_create_argv,
    prep_run_argv,
    repo_label,
    volume_name,
    worker_create_argv,
)


def test_default_image_and_pinned_digest():
    assert DEFAULT_IMAGE == "ghcr.io/jimboschneider/dirtywork-worker:0.6"
    # Unset for 0.6.0: the :0.6 image is first published by the v0.6.0
    # release itself, so the pin follows in 0.6.1 (docker/README.md
    # documents how to resolve and re-pin it whenever :0.6 is re-pushed).
    assert PINNED_DIGEST is None


def test_path_env_is_standard_unix_path():
    assert PATH_ENV == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def test_docker_config_defaults():
    cfg = DockerConfig()
    assert cfg.image == DEFAULT_IMAGE
    assert cfg.network == "none"
    assert cfg.memory == "4g"
    assert cfg.cpus == "2"
    assert cfg.pids_limit == 512
    assert cfg.tmp_size == "1g"
    assert cfg.gitdir_size == "512m"
    assert cfg.home_size == "256m"
    assert cfg.max_worktree_mb == 2048
    assert cfg.max_worktree_files == 200_000
    assert cfg.min_free_mb == 2048
    assert cfg.max_patch_mb == 10
    assert cfg.keep_volume is False


def test_container_and_volume_names():
    assert container_name("abc123") == "dw-abc123"
    assert volume_name("abc123") == "dw-abc123-work"


def test_repo_label_is_sha256_of_resolved_path(tmp_path: Path):
    import hashlib
    expected = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()
    assert repo_label(tmp_path) == expected


def test_exec_argv_default_workdir_no_stdin():
    argv = exec_argv("dw-slug", ["/usr/bin/git", "status"])
    assert argv == ["exec", "-w", "/work", "dw-slug", "/usr/bin/git", "status"]


def test_exec_argv_with_stdin_flag():
    argv = exec_argv("dw-slug", ["/bin/sh", "-c", "cat"], stdin=True)
    assert argv == ["exec", "-w", "/work", "-i", "dw-slug", "/bin/sh", "-c", "cat"]


def test_exec_argv_with_env_and_custom_workdir():
    argv = exec_argv("dw-slug", ["/bin/true"], workdir="/gitdir",
                      env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    assert argv == ["exec", "-w", "/gitdir",
                     "-e", "GIT_CONFIG_GLOBAL=/dev/null",
                     "-e", "GIT_CONFIG_NOSYSTEM=1",
                     "dw-slug", "/bin/true"]


def test_prep_run_argv_exact():
    cfg = DockerConfig()
    argv = prep_run_argv(cfg, "abc123", "dirtywork/worker@sha256:" + "a" * 64, 501, 20)
    assert argv == [
        "run", "--rm", "--network", "none", "--user", "0:0",
        "--cap-drop", "ALL", "--cap-add", "CHOWN",
        "--mount", "type=volume,src=dw-abc123-work,dst=/work",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/chown",
        "dirtywork/worker@sha256:" + "a" * 64,
        "501:20", "/work",
    ]
    assert "-w" not in argv


def test_worker_create_argv_exact():
    cfg = DockerConfig()
    image_ref = "dirtywork/worker@sha256:" + "a" * 64
    argv = worker_create_argv(cfg, "abc123", image_ref, 501, 20,
                               Path("/Users/x/repo/.git/objects"),
                               repo_label="deadbeef" * 8)
    assert argv == [
        "create", "-i", "--init", "--name", "dw-abc123",
        "--label", "dirtywork.run=abc123",
        "--label", f"dirtywork.repo={'deadbeef' * 8}",
        "--network", "none",
        "--memory", "4g", "--memory-swap", "4g", "--cpus", "2",
        "--pids-limit", "512",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "501:20",
        "--tmpfs", "/tmp:rw,exec,size=1g,mode=1777",
        "--tmpfs", "/gitdir:rw,size=512m,mode=0700,uid=501,gid=20",
        "--tmpfs", "/home/worker:rw,size=256m,mode=0700,uid=501,gid=20",
        "--mount", "type=volume,src=dw-abc123-work,dst=/work",
        "--mount", "type=bind,src=/Users/x/repo/.git/objects,dst=/repo.git/objects,readonly",
        "-e", "GIT_DIR=/gitdir",
        "-e", "GIT_WORK_TREE=/work",
        "-e", "HOME=/home/worker",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "-e", "GIT_AUTHOR_NAME=dirtywork",
        "-e", "GIT_AUTHOR_EMAIL=dirtywork@localhost",
        "-e", "GIT_COMMITTER_NAME=dirtywork",
        "-e", "GIT_COMMITTER_EMAIL=dirtywork@localhost",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/cat",
        image_ref,
    ]
    assert "-w" not in argv


def test_worker_create_argv_honors_custom_network_and_sizes():
    cfg = DockerConfig(network="bridge", memory="8g", cpus="4", pids_limit=256,
                        tmp_size="2g", gitdir_size="1g", home_size="512m")
    argv = worker_create_argv(cfg, "s", "img@sha256:" + "0" * 64, 1000, 1000,
                               Path("/repo/.git/objects"), repo_label="x")
    assert "--network" in argv and argv[argv.index("--network") + 1] == "bridge"
    assert "--memory" in argv and argv[argv.index("--memory") + 1] == "8g"
    assert "--memory-swap" in argv and argv[argv.index("--memory-swap") + 1] == "8g"
    assert "--cpus" in argv and argv[argv.index("--cpus") + 1] == "4"
    assert "--pids-limit" in argv and argv[argv.index("--pids-limit") + 1] == "256"
    assert "/tmp:rw,exec,size=2g,mode=1777" in argv
    assert "/gitdir:rw,size=1g,mode=0700,uid=1000,gid=1000" in argv
    assert "/home/worker:rw,size=512m,mode=0700,uid=1000,gid=1000" in argv


def test_export_create_argv_always_network_none_even_with_bridge_cfg():
    cfg = DockerConfig(network="bridge")  # --allow-network was passed
    image_ref = "dirtywork/worker@sha256:" + "a" * 64
    argv = export_create_argv(cfg, "abc123", image_ref, 501, 20,
                               Path("/repo/.git/objects"), repo_label="deadbeef")
    assert "--network" in argv
    assert argv[argv.index("--network") + 1] == "none"


def test_export_create_argv_exact():
    cfg = DockerConfig()
    image_ref = "dirtywork/worker@sha256:" + "a" * 64
    argv = export_create_argv(cfg, "abc123", image_ref, 501, 20,
                               Path("/repo/.git/objects"), repo_label="deadbeef")
    assert argv == [
        "create", "-i", "--init", "--name", "dw-abc123-export",
        "--label", "dirtywork.run=abc123",
        "--label", "dirtywork.repo=deadbeef",
        "--network", "none",
        "--memory", "4g", "--memory-swap", "4g", "--cpus", "2",
        "--pids-limit", "256",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "501:20",
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--tmpfs", "/gitdir:rw,size=2g,mode=0700,uid=501,gid=20",
        "--tmpfs", "/home/worker:rw,size=64m,mode=0700,uid=501,gid=20",
        "--mount", "type=volume,src=dw-abc123-work,dst=/work,readonly",
        "--mount", "type=bind,src=/repo/.git/objects,dst=/repo.git/objects,readonly",
        "-e", "GIT_DIR=/gitdir",
        "-e", "GIT_WORK_TREE=/work",
        "-e", "HOME=/home/worker",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "-e", "GIT_AUTHOR_NAME=dirtywork",
        "-e", "GIT_AUTHOR_EMAIL=dirtywork@localhost",
        "-e", "GIT_COMMITTER_NAME=dirtywork",
        "-e", "GIT_COMMITTER_EMAIL=dirtywork@localhost",
        "-e", f"PATH={PATH_ENV}",
        "--entrypoint", "/bin/cat",
        image_ref,
    ]
    assert "-w" not in argv
