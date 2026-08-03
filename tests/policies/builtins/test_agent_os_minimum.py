"""Minimum policy bundle required by the disposable Agent OS fit test."""

from __future__ import annotations

from omnigent.policies.builtins.github import github_policy
from omnigent.policies.builtins.safety import ask_on_os_tools, enforce_sandbox
from omnigent.policies.builtins.working_dir import block_working_dir_changes
from tests.policies.builtins.helpers import tool_call_event as tc

_WORKSPACE = "/Users/hafizrazali/Projects/Omnigent-fit-test-project"
_WORKTREES = "/Users/hafizrazali/Projects/Omnigent-fit-test-project-worktrees"
_ROOTS = [_WORKSPACE, _WORKTREES]


def test_os_access_requires_approval() -> None:
    result = ask_on_os_tools(tc("Bash", {"command": "ls"}))

    assert result["result"] == "ASK"


def test_working_directory_policy_allows_only_the_disposable_roots() -> None:
    policy = block_working_dir_changes(
        block_cd=True,
        block_worktree=True,
        allowed_dirs=_ROOTS,
        action="deny",
    )

    assert policy(tc("Bash", {"command": f"cd {_WORKSPACE}/nested"})) is None
    assert policy(tc("Bash", {"command": f"cd {_WORKTREES}/feature-one"})) is None
    assert (
        policy(tc("Bash", {"command": "cd /Users/hafizrazali/Projects/Sifututor"}))["result"]
        == "DENY"
    )
    assert policy(tc("Bash", {"command": "git worktree add ../escape test"}))["result"] == "DENY"


def test_github_policy_allows_reads_but_denies_all_writes() -> None:
    policy = github_policy(read_all=True, write_repos=[])

    assert policy(tc("mcp__github__get_file_contents", {"owner": "o", "repo": "r"})) is None
    result = policy(tc("mcp__github__create_issue", {"owner": "o", "repo": "r"}))
    assert result is not None and result["result"] == "DENY"


def test_sandbox_is_forced_to_the_disposable_roots_without_network() -> None:
    policy = enforce_sandbox(
        sandbox_type="darwin_seatbelt",
        allow_network=False,
        write_paths=_ROOTS,
        read_paths=_ROOTS,
        env_passthrough=[],
    )

    result = policy(
        tc(
            "sys_agent_start",
            {"agent_name": "fit-test", "harness": "codex", "sandbox": None},
        )
    )

    assert result["result"] == "ALLOW"
    assert result["data"]["arguments"]["sandbox"] == {
        "type": "darwin_seatbelt",
        "allow_network": False,
        "write_paths": _ROOTS,
        "read_paths": _ROOTS,
        "env_passthrough": [],
    }
