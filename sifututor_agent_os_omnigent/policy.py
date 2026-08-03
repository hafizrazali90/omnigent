"""Agent OS approval, evidence, and critical-lane workflow policy."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from omnigent.policies.builtins.orchestration import blast_radius
from omnigent.policies.schema import PolicyEvent, PolicyResponse, request_user_text

try:
    _shell = import_module("omnigent.policies.shell")
except ModuleNotFoundError:
    # Omnigent 0.7 exposes the same parser privately. Keep the adapter's
    # declared 0.7 compatibility truthful while the public seam is upstreamed.
    _shell = import_module("omnigent.policies.builtins._shell")

MAX_SHELL_NESTING = _shell.MAX_SHELL_NESTING
real_invocation_tokens = _shell.real_invocation_tokens
split_command_segments = _shell.split_command_segments
unwrap_shell_command = _shell.unwrap_shell_command

_READ_TOOLS = frozenset(
    {"Read", "read", "read_file", "sys_os_read", "developer__read", "developer__read_file"}
)
_WRITE_TOOLS = frozenset(
    {
        "Write",
        "Edit",
        "MultiEdit",
        "write",
        "edit",
        "write_file",
        "sys_os_write",
        "sys_os_edit",
        "developer__write",
        "developer__edit",
    }
)
_DEFAULT_SHELL_TOOLS = frozenset(
    {"Bash", "bash", "Shell", "terminal", "sys_os_shell", "developer__shell"}
)
_OUTWARD_TOOL_WORDS = frozenset(
    {"create_pull_request", "merge_pull_request", "push_files", "deploy", "delete_repo"}
)
_CRITICAL_TERMS = {
    "payment": re.compile(r"\b(payment|refund)\b", re.IGNORECASE),
    "invoice": re.compile(r"\binvoice\b", re.IGNORECASE),
    "commission": re.compile(r"\bcommission\b", re.IGNORECASE),
    "migration": re.compile(r"\b(migration|migrate)\b", re.IGNORECASE),
    "auth": re.compile(r"\bauth(?:entication|orization)?\b", re.IGNORECASE),
    "mobile-api": re.compile(r"\bmobile\s+api(?:\s+contract)?\b", re.IGNORECASE),
    "production-deploy": re.compile(
        r"\b(production\s+deploy|deploy(?:ment)?(?:\s+\w+){0,3}\s+production)\b",
        re.IGNORECASE,
    ),
}
_CRITICAL_ACTION_RE = re.compile(
    r"\b(implement|fix|change|modify|run|apply|create|update|build|deploy|migrate)\b",
    re.IGNORECASE,
)
_PHASE_B_APPROVAL_RE = re.compile(r"\bapprove\s+phase\s+b\s+implementation\b", re.IGNORECASE)
_EVIDENCE_CLAIM_RE = re.compile(
    r"\b(?:all\s+)?(?:tests?|checks?|verification|guard)\s+(?:passed|pass|green)\b|"
    r"\b(?:change|work|implementation)\s+is\s+verified\b",
    re.IGNORECASE,
)
_MUTATING_CRITICAL_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:rm|mv|cp|tee|touch|mkdir|chmod|chown)\b|"
    r"\bsed\s+-i\b|(?:^|\s)(?:>|>>)(?:\s|$)|"
    r"\bgit\s+(?:add|commit|merge|rebase|push|reset|clean)\b|"
    r"\b(?:npm|pnpm|yarn|pip|uv)\s+(?:install|add|remove)\b|"
    r"\b(?:migrate|deploy|apply|destroy)\b",
    re.IGNORECASE,
)
_SHELL_FILE_MUTATION_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:rm|mv|cp|tee|touch|mkdir|chmod|chown)\b|"
    r"\bsed\s+(?:-i(?:\S*)?|--in-place(?:=\S*)?)(?:\s|$)|"
    r"(?:^|\s)(?:>|>>)(?:\s|$)|"
    r"\b(?:npm|pnpm|yarn|pip|uv)\s+(?:install|add|remove)\b|"
    r"\b(?:migrate|deploy|apply|destroy)\b",
    re.IGNORECASE,
)
_SHELL_DYNAMIC_EXECUTION_RE = re.compile(r"\$\(|`|(?:^|\s)[<>]\(|<<-?\s*['\"]?\w")
_SHELL_EGRESS_RE = re.compile(
    r"\b(?:nc|netcat|scp|sftp)\b|"
    r"\bcurl\b[^;&|]*(?:\s(?:-d|--data(?:-raw|-binary|-urlencode)?|-F|--form|-T|--upload-file)\b)",
    re.IGNORECASE,
)
_SENSITIVE_ENV_EXPANSION_RE = re.compile(
    r"\$(?:\{)?[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*(?:\})?",
    re.IGNORECASE,
)
_GIT_ENV_OVERRIDE_RE = re.compile(r"(?:^|\s)GIT_[A-Z0-9_]+=", re.IGNORECASE)
_COMMIT_PRODUCING_GIT_COMMANDS = frozenset(
    {"am", "commit-tree", "merge", "rebase", "cherry-pick", "revert"}
)
_FORBIDDEN_GIT_MUTATIONS = frozenset(
    {"apply", "checkout", "clean", "config", "reset", "restore", "rm", "stash", "update-index"}
)
_SAFE_GIT_READ_COMMANDS = frozenset(
    {
        "cat-file",
        "describe",
        "diff",
        "grep",
        "log",
        "ls-files",
        "name-rev",
        "rev-parse",
        "show",
        "status",
    }
)
_SAFE_SHELL_READ_COMMANDS = frozenset(
    {
        "cat",
        "cut",
        "date",
        "file",
        "find",
        "grep",
        "head",
        "jq",
        "ls",
        "lsof",
        "pgrep",
        "ps",
        "pwd",
        "rg",
        "sed",
        "sort",
        "stat",
        "tail",
        "tr",
        "uniq",
        "wc",
        "which",
    }
)
_GIT_GLOBAL_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
)
_STALE_AFTER_WRITE_KEYS = (
    "agent_os_guard_evidence",
    "agent_os_verify_evidence",
    "agent_os_staged_paths",
    "agent_os_evidence_commands",
)

_Json: TypeAlias = dict[str, Any]  # type: ignore[explicit-any]


def _decision(result: Literal["ALLOW", "DENY", "ASK"], reason: str) -> PolicyResponse:
    return {"result": result, "reason": reason}


def _tool(event: PolicyEvent) -> tuple[str, _Json] | None:
    if event.get("type") != "tool_call":
        return None
    data = event.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None
    args = data.get("arguments")
    return data["name"], args if isinstance(args, dict) else {}


def _paths_from_args(args: _Json) -> list[str]:
    paths = [args.get("path"), args.get("file_path")]
    edits = args.get("edits")
    if isinstance(edits, list):
        paths.extend(
            edit.get("file_path") or edit.get("path") for edit in edits if isinstance(edit, dict)
        )
    return [path for path in paths if isinstance(path, str) and path.strip()]


def _is_env_path(path: str) -> bool:
    return Path(path).name.startswith(".env")


def _is_sensitive_path(path: str) -> bool:
    candidate = Path(path)
    lowered_parts = {part.lower() for part in candidate.parts}
    name = candidate.name.lower()
    return (
        _is_env_path(path)
        or bool(lowered_parts & {".ssh", ".aws", ".gnupg", "secrets"})
        or name in {"credentials.json", "secrets.json", "secrets.yaml", "secrets.yml"}
        or name.startswith(("id_rsa", "id_ed25519"))
    )


def _resolve_path(path: str, primary_root: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = primary_root / candidate
    return candidate.resolve(strict=False)


def _inside_roots(path: str, roots: tuple[Path, ...], workspace_root: Path) -> bool:
    try:
        resolved = _resolve_path(path, workspace_root)
        return any(resolved.is_relative_to(root) for root in roots)
    except (OSError, RuntimeError, ValueError):
        return False


def _shell_token_groups(command: str, depth: int = 0) -> list[list[str]]:
    if depth > MAX_SHELL_NESTING:
        return []
    groups: list[list[str]] = []
    for segment in split_command_segments(command):
        try:
            tokens = real_invocation_tokens(shlex.split(segment))
        except ValueError:
            tokens = segment.split()
        if not tokens:
            continue
        inner = unwrap_shell_command(tokens)
        if inner is not None:
            groups.extend(_shell_token_groups(inner, depth + 1))
        else:
            groups.append(tokens)
    return groups


def _git_command(tokens: list[str]) -> tuple[str | None, list[str]]:
    if not tokens or tokens[0] != "git":
        return None, []
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in _GIT_GLOBAL_VALUE_OPTIONS and i + 1 < len(tokens) else 1
    if i >= len(tokens):
        return None, []
    return tokens[i], tokens[i + 1 :]


def _git_uses_forbidden_overrides(tokens: list[str]) -> bool:
    if not tokens or tokens[0] != "git":
        return False
    return any(
        token in {"-c", "--config-env", "-C", "--git-dir", "--work-tree"}
        or token.startswith(("--config-env=", "--git-dir=", "--work-tree="))
        for token in tokens[1:]
    )


def _broad_add(tokens: list[str]) -> bool:
    subcommand, args = _git_command(tokens)
    return subcommand == "add" and any(
        arg in {".", "./", "-A", "--all", "-u", "--update"} for arg in args
    )


def _git_add_paths(tokens: list[str]) -> list[str] | None:
    subcommand, args = _git_command(tokens)
    if subcommand != "add":
        return None
    return [arg for arg in args if arg != "--" and not arg.startswith("-")]


def _is_commit(tokens: list[str]) -> bool:
    subcommand, _args = _git_command(tokens)
    return subcommand == "commit"


def _commit_has_forbidden_flags(tokens: list[str]) -> bool:
    subcommand, args = _git_command(tokens)
    if subcommand != "commit":
        return False
    for arg in args:
        if arg in {"-a", "-i", "-n", "-o", "-p"}:
            return True
        long_option = arg.split("=", 1)[0]
        if long_option.startswith("--") and any(
            forbidden.startswith(long_option)
            for forbidden in {
                "--all",
                "--amend",
                "--include",
                "--interactive",
                "--no-verify",
                "--only",
                "--patch",
                "--pathspec-file-nul",
                "--pathspec-from-file",
            }
            if len(long_option) >= 4
        ):
            return True
        if arg.startswith("-") and not arg.startswith("--"):
            flags = arg[1:]
            if "a" in flags or "n" in flags:
                return True
    return False


def _commit_has_pathspec(tokens: list[str]) -> bool:
    subcommand, args = _git_command(tokens)
    if subcommand != "commit":
        return False
    value_options = {
        "-C",
        "-c",
        "-F",
        "-m",
        "--author",
        "--cleanup",
        "--date",
        "--file",
        "--fixup",
        "--message",
        "--reedit-message",
        "--reuse-message",
        "--squash",
        "--trailer",
    }
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg == "--":
            return True
        if arg in value_options:
            skip_value = True
            continue
        if arg.startswith("-"):
            continue
        return True
    return False


def _forbidden_index_mutation(tokens: list[str]) -> bool:
    subcommand, args = _git_command(tokens)
    if subcommand in {"rm", "reset", "restore", "update-index"}:
        return True
    return subcommand == "apply" and any(arg in {"--cached", "--index"} for arg in args)


def _commit_producing_shortcut(tokens: list[str]) -> bool:
    subcommand, _args = _git_command(tokens)
    return subcommand in _COMMIT_PRODUCING_GIT_COMMANDS


def _forbidden_git_mutation(tokens: list[str]) -> bool:
    subcommand, _args = _git_command(tokens)
    return subcommand in _FORBIDDEN_GIT_MUTATIONS


def _safe_git_read(tokens: list[str]) -> bool:
    subcommand, args = _git_command(tokens)
    if subcommand in _SAFE_GIT_READ_COMMANDS:
        return True
    if subcommand == "branch":
        return not args or all(
            arg in {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--list", "--show-current"}
            or arg.startswith("--format=")
            for arg in args
        )
    if subcommand == "remote":
        return not args or args == ["-v"] or args == ["--verbose"]
    if subcommand == "worktree":
        return bool(args) and args[0] == "list"
    if subcommand == "tag":
        return not args or all(
            arg in {"-l", "--list"} or arg.startswith("--format=") for arg in args
        )
    return False


def _shell_mentions_sensitive_path(tokens: list[str]) -> bool:
    for token in tokens[1:]:
        value = token.rstrip(";,")
        if value.startswith("--") and "=" in value:
            value = value.split("=", 1)[1]
        if _is_sensitive_path(value):
            return True
    return False


def _shell_enumerates_environment(tokens: list[str]) -> bool:
    if not tokens:
        return False
    executable = Path(tokens[0]).name
    return executable in {"env", "printenv", "set"} or (
        executable in {"export", "declare"}
        and (len(tokens) == 1 or any(arg in {"-p", "-x"} for arg in tokens[1:]))
    )


def _has_unquoted_shell_redirection(command: str) -> bool:
    """Return whether *command* contains an unquoted output redirect."""
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == ">":
            return True
    return False


def _shell_path_outside_roots(
    tokens: list[str], roots: tuple[Path, ...], workspace_root: Path
) -> bool:
    if tokens and Path(tokens[0]).name in {"cd", "pushd", "popd"}:
        return True
    if tokens and "/" in tokens[0]:
        executable = Path(tokens[0]).expanduser()
        trusted_system_roots = (Path("/bin"), Path("/usr/bin"), Path("/opt/homebrew/bin"))
        if not any(
            executable.is_relative_to(root) for root in trusted_system_roots
        ) and not _inside_roots(tokens[0], roots, workspace_root):
            return True
    for token in tokens[1:]:
        value = token.rstrip(";,")
        if value.startswith("--") and "=" in value:
            value = value.split("=", 1)[1]
        if not value or value.startswith("-") or "://" in value:
            continue
        if value in {">", ">>", "<", "2>", "2>>", "/dev/null"}:
            continue
        if value == ".." or value.startswith(("/", "./", "../", "~")) or "/" in value:
            if not _inside_roots(value, roots, workspace_root):
                return True
    return False


def _commit_evidence_complete(state: _Json) -> bool:
    if not isinstance(state.get("agent_os_guard_evidence"), str):
        return False
    if not isinstance(state.get("agent_os_verify_evidence"), str):
        return False
    staged = state.get("agent_os_staged_paths")
    approved = state.get("agent_os_approved_write_paths")
    return (
        isinstance(staged, list)
        and isinstance(approved, list)
        and bool(staged)
        and set(staged) == set(approved)
    )


def _tool_result(event: PolicyEvent) -> tuple[str, _Json, str, bool] | None:
    """Return the originating tool, arguments, output, and trusted success."""
    if event.get("type") != "tool_result":
        return None
    request = event.get("request_data")
    data = event.get("data")
    if not isinstance(request, dict) or not isinstance(request.get("name"), str):
        return None
    args = request.get("arguments")
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if not isinstance(args, dict) or not isinstance(result, str):
        return None
    return request["name"], args, result, data.get("succeeded") is True


def _append_evidence(state: _Json, command: str) -> list[str]:
    current = state.get("agent_os_evidence_commands")
    commands = (
        [item for item in current if isinstance(item, str)] if isinstance(current, list) else []
    )
    return commands if command in commands else [*commands, command]


def _single_shell_invocation(command: str) -> list[str] | None:
    groups = _shell_token_groups(command)
    segments = [segment for segment in split_command_segments(command) if segment.strip()]
    return groups[0] if len(groups) == 1 and len(segments) == 1 else None


def _is_guard_command(command: str, guard_path: Path, workspace_root: Path) -> bool:
    tokens = _single_shell_invocation(command)
    if not tokens:
        return False
    candidate = tokens[0]
    if Path(candidate).name in {"bash", "sh", "zsh"} and len(tokens) == 2:
        candidate = tokens[1]
    elif len(tokens) != 1:
        return False
    try:
        return _resolve_path(candidate, workspace_root) == guard_path
    except (OSError, RuntimeError, ValueError):
        return False


def _is_verify_command(command: str) -> bool:
    tokens = _single_shell_invocation(command)
    if not tokens:
        return False
    executable = Path(tokens[0]).name
    args = tokens[1:]
    if len(args) >= 2 and (
        (executable == "uv" and args[0] == "run")
        or (executable.startswith("python") and args[0] == "-m")
    ):
        executable, args = Path(args[1]).name, args[2:]
    informational = {"--help", "-h", "--version", "-V", "--collect-only", "--co"}
    if any(arg in informational for arg in args):
        return False
    if executable in {"pytest", "phpunit", "playwright"}:
        return bool(args)
    if executable == "ruff":
        return bool(args) and (args[0] == "check" or (args[0] == "format" and "--check" in args))
    if executable == "mypy":
        return any(not arg.startswith("-") for arg in args)
    return executable in {"npm", "pnpm", "yarn"} and any(
        token in {"test", "lint", "build", "typecheck"} for token in args
    )


def _is_known_safe_shell_command(command: str, guard_path: Path, workspace_root: Path) -> bool:
    if _is_guard_command(command, guard_path, workspace_root) or _is_verify_command(command):
        return True
    groups = _shell_token_groups(command)
    if not groups:
        return False
    for tokens in groups:
        executable = Path(tokens[0]).name
        if executable == "git":
            if _is_staged_inventory_command(command) or _safe_git_read(tokens):
                continue
            if _git_add_paths(tokens) is not None or _is_commit(tokens):
                continue
            return False
        if executable not in _SAFE_SHELL_READ_COMMANDS:
            return False
        if executable == "sed" and any(arg.startswith(("-i", "--in-place")) for arg in tokens[1:]):
            return False
        if executable == "find" and any(
            arg in {"-delete", "-exec", "-execdir", "-ok"} for arg in tokens[1:]
        ):
            return False
    return True


def _is_staged_inventory_command(command: str) -> bool:
    tokens = _single_shell_invocation(command)
    return tokens is not None and _git_command(tokens) == ("diff", ["--cached", "--name-only"])


def _observed_staged_paths(
    output: str, roots: tuple[Path, ...], workspace_root: Path
) -> list[str] | None:
    paths: list[str] = []
    for line in output.splitlines():
        value = line.strip()
        if not value:
            continue
        resolved = _resolve_path(value, workspace_root)
        if not any(resolved.is_relative_to(root) for root in roots):
            return None
        paths.append(str(resolved))
    return paths


def _invalidate_write_evidence() -> list[_Json]:
    return [{"key": key, "action": "delete"} for key in _STALE_AFTER_WRITE_KEYS]


def agent_os_workflow_guard(
    *,
    allowed_roots: list[str],
    guard_command_path: str,
    workspace_root: str | None = None,
    shell_tools: list[str] | None = None,
) -> Callable[[PolicyEvent], PolicyResponse | None]:
    """Build the fixture-backed Agent OS workflow guard.

    This policy adds approval/evidence semantics around Omnigent's existing
    sandbox, directory, GitHub, and blast-radius controls. It deliberately
    stores approval and evidence as explicit session state rather than trying
    to infer them from conversational wording.
    """
    if not allowed_roots:
        raise ValueError("agent_os_workflow_guard requires at least one allowed root")
    roots = tuple(Path(root).expanduser().resolve(strict=False) for root in allowed_roots)
    task_root = _resolve_path(workspace_root or str(roots[0]), roots[0])
    if not any(task_root.is_relative_to(root) for root in roots):
        raise ValueError("workspace_root must be inside an allowed root")
    guard_path = _resolve_path(guard_command_path, task_root)
    if not any(guard_path.is_relative_to(root) for root in roots):
        raise ValueError("guard_command_path must be inside an allowed root")
    shell_names = frozenset(shell_tools) if shell_tools is not None else _DEFAULT_SHELL_TOOLS
    deny_blast_radius = blast_radius(gate_pushes=True, risky_action="DENY")

    def _evaluate(event: PolicyEvent) -> PolicyResponse | None:
        state = event.get("session_state")
        state = state if isinstance(state, dict) else {}

        observed = _tool_result(event)
        if observed is not None:
            name, args, output, succeeded = observed
            command = args.get("command")
            if name not in shell_names or not isinstance(command, str):
                return None
            if not succeeded:
                return None
            updates: list[_Json] = []
            if _is_guard_command(command, guard_path, task_root):
                updates.append(
                    {"key": "agent_os_guard_evidence", "action": "set", "value": command}
                )
            if _is_verify_command(command):
                updates.append(
                    {"key": "agent_os_verify_evidence", "action": "set", "value": command}
                )
            if _is_staged_inventory_command(command):
                staged_paths = _observed_staged_paths(output, roots, task_root)
                if staged_paths is None:
                    return None
                updates.append(
                    {
                        "key": "agent_os_staged_paths",
                        "action": "set",
                        "value": staged_paths,
                    }
                )
            if updates:
                updates.append(
                    {
                        "key": "agent_os_evidence_commands",
                        "action": "set",
                        "value": _append_evidence(state, command),
                    }
                )
                return cast(
                    PolicyResponse,
                    {
                        "result": "ALLOW",
                        "reason": (
                            "Recorded fresh evidence from an observed successful tool result."
                        ),
                        "state_updates": updates,
                    },
                )
            return None

        if event.get("type") == "request":
            request_text = request_user_text(event.get("data"))
            current_scopes = state.get("agent_os_critical_scopes")
            if not isinstance(current_scopes, list):
                current_scope = state.get("agent_os_critical_scope")
                current_scopes = [current_scope] if isinstance(current_scope, str) else []
            if (
                state.get("agent_os_critical_lane") is True
                and current_scopes
                and _PHASE_B_APPROVAL_RE.search(request_text)
                and all(_CRITICAL_TERMS[scope].search(request_text) for scope in current_scopes)
            ):
                prior_approved = state.get("agent_os_critical_phase_b_approved")
                approved_scopes = (
                    {scope for scope in prior_approved if isinstance(scope, str)}
                    if isinstance(prior_approved, list)
                    else set()
                )
                approved_scopes.update(current_scopes)
                return {
                    "result": "ALLOW",
                    "reason": "Human approved Phase B for the matching critical scope.",
                    "state_updates": [
                        {"key": "agent_os_critical_lane", "action": "delete"},
                        {"key": "agent_os_critical_scope", "action": "delete"},
                        {"key": "agent_os_critical_scopes", "action": "delete"},
                        {
                            "key": "agent_os_critical_phase_b_approved",
                            "action": "set",
                            "value": sorted(approved_scopes),
                        },
                    ],
                }
            critical_scopes = sorted(
                scope for scope, pattern in _CRITICAL_TERMS.items() if pattern.search(request_text)
            )
            approved = state.get("agent_os_critical_phase_b_approved")
            approved_scopes = (
                {scope for scope in approved if isinstance(scope, str)}
                if isinstance(approved, list)
                else set()
            )
            unapproved_scopes = [
                scope for scope in critical_scopes if scope not in approved_scopes
            ]
            if unapproved_scopes and _CRITICAL_ACTION_RE.search(request_text):
                return {
                    "result": "ALLOW",
                    "reason": "Critical lane detected; implementation remains blocked.",
                    "state_updates": [
                        {"key": "agent_os_critical_lane", "action": "set", "value": True},
                        {
                            "key": "agent_os_critical_scope",
                            "action": "set",
                            "value": unapproved_scopes[0],
                        },
                        {
                            "key": "agent_os_critical_scopes",
                            "action": "set",
                            "value": unapproved_scopes,
                        },
                    ],
                }
            return None

        if event.get("type") == "response":
            response = event.get("data")
            evidence = state.get("agent_os_evidence_commands")
            if (
                isinstance(response, str)
                and _EVIDENCE_CLAIM_RE.search(response)
                and not (isinstance(evidence, list) and evidence)
            ):
                return _decision("DENY", "A passed/verified claim requires named fresh evidence.")
            return None

        tool_call = _tool(event)
        if tool_call is None:
            return None
        name, args = tool_call
        paths = _paths_from_args(args)

        if name in _READ_TOOLS | _WRITE_TOOLS:
            if any(_is_sensitive_path(path) for path in paths):
                return _decision("DENY", "Agent OS never permits direct access to secret files.")
            if not paths or any(not _inside_roots(path, roots, task_root) for path in paths):
                return _decision("DENY", "File operation is outside the approved Agent OS roots.")

        if name in _WRITE_TOOLS:
            if state.get("agent_os_critical_lane") is True:
                return _decision("DENY", "Critical-lane work is diagnosis and planning only.")
            approved_paths = state.get("agent_os_approved_write_paths")
            approved_values = approved_paths if isinstance(approved_paths, list) else []
            approved = {
                str(_resolve_path(path, task_root))
                for path in approved_values
                if isinstance(path, str)
            }
            requested = {str(_resolve_path(path, task_root)) for path in paths}
            missing = requested - approved
            if missing:
                combined = sorted(approved | requested)
                target_list = ", ".join(sorted(missing))
                return cast(
                    PolicyResponse,
                    {
                        "result": "ASK",
                        "reason": (
                            f"Approve implementation for these exact file paths: {target_list}."
                        ),
                        "state_updates": [
                            {
                                "key": "agent_os_approved_write_paths",
                                "action": "set",
                                "value": combined,
                            },
                            *_invalidate_write_evidence(),
                        ],
                    },
                )
            return cast(
                PolicyResponse,
                {
                    "result": "ALLOW",
                    "reason": "Approved write invalidates prior commit evidence.",
                    "state_updates": _invalidate_write_evidence(),
                },
            )

        lowered_name = name.lower()
        if any(word in lowered_name for word in _OUTWARD_TOOL_WORDS):
            return _decision("DENY", "Outward actions are disabled in the Agent OS fit test.")

        if name not in shell_names:
            return None
        command = args.get("command")
        if not isinstance(command, str):
            return _decision(
                "DENY", "A shell request without a command cannot be evaluated safely."
            )
        groups = _shell_token_groups(command)
        if _SHELL_DYNAMIC_EXECUTION_RE.search(command):
            return _decision("DENY", "Dynamic shell execution and heredocs are forbidden.")
        if _has_unquoted_shell_redirection(command):
            return _decision("DENY", "Shell redirection may not bypass approval-aware file tools.")
        if _SHELL_EGRESS_RE.search(command):
            return _decision("DENY", "Shell data upload and raw network egress are forbidden.")
        if _GIT_ENV_OVERRIDE_RE.search(command):
            return _decision("DENY", "Git environment overrides are forbidden.")
        if _SENSITIVE_ENV_EXPANSION_RE.search(command):
            return _decision("DENY", "Sensitive environment expansion is forbidden.")
        if any(_shell_mentions_sensitive_path(tokens) for tokens in groups):
            return _decision("DENY", "Shell access to secret files is forbidden.")
        if any(_shell_enumerates_environment(tokens) for tokens in groups):
            return _decision("DENY", "Broad environment enumeration is forbidden.")
        if any(_shell_path_outside_roots(tokens, roots, task_root) for tokens in groups):
            return _decision("DENY", "Shell path is outside the approved Agent OS roots.")
        if _SHELL_FILE_MUTATION_RE.search(command):
            return _decision(
                "DENY", "Shell file mutation is forbidden; use an approval-aware file tool."
            )
        if any(_git_uses_forbidden_overrides(tokens) for tokens in groups):
            return _decision("DENY", "Git directory and configuration overrides are forbidden.")
        if any(_commit_producing_shortcut(tokens) for tokens in groups):
            return _decision("DENY", "Only the reviewed git commit path may create a commit.")
        if any(_forbidden_git_mutation(tokens) for tokens in groups):
            return _decision("DENY", "This git mutation bypasses the reviewed workflow path.")
        if state.get("agent_os_critical_lane") is True and _MUTATING_CRITICAL_RE.search(command):
            return _decision(
                "DENY", "Critical-lane shell mutation is blocked pending Phase B approval."
            )
        if any(_broad_add(tokens) for tokens in groups):
            return _decision("DENY", "Broad staging is forbidden; stage exact approved paths.")
        approved_paths = state.get("agent_os_approved_write_paths")
        approved_values = approved_paths if isinstance(approved_paths, list) else []
        approved = {
            str(_resolve_path(path, task_root))
            for path in approved_values
            if isinstance(path, str)
        }
        for tokens in groups:
            add_paths = _git_add_paths(tokens)
            if add_paths is None:
                continue
            requested = {str(_resolve_path(path, task_root)) for path in add_paths}
            if not requested or not requested.issubset(approved):
                return _decision(
                    "DENY", "Git may stage only explicit paths from the approved file bundle."
                )
        if any(_commit_has_forbidden_flags(tokens) for tokens in groups):
            return _decision("DENY", "Commit broadening and hook-bypass flags are forbidden.")
        if any(_commit_has_pathspec(tokens) for tokens in groups):
            return _decision(
                "DENY", "Commit pathspecs may not broaden the reviewed staged bundle."
            )
        if any(_forbidden_index_mutation(tokens) for tokens in groups):
            return _decision("DENY", "Only exact git add operations may change the staged bundle.")
        if any(_git_add_paths(tokens) is not None for tokens in groups):
            return {
                "result": "ALLOW",
                "reason": "Staging changed; a fresh staged inventory is required.",
                "state_updates": [{"key": "agent_os_staged_paths", "action": "delete"}],
            }
        if any(_is_commit(tokens) for tokens in groups) and not _commit_evidence_complete(state):
            return _decision(
                "DENY",
                "Commit requires observed guard and verify evidence plus exact staged paths.",
            )
        if any(_is_commit(tokens) for tokens in groups):
            staged = state.get("agent_os_staged_paths")
            return _decision(
                "ASK", f"Approve this local commit for the exact staged paths: {staged}."
            )
        blast = deny_blast_radius(cast(_Json, event), {})
        if blast.get("result") == "DENY":
            return _decision("DENY", str(blast.get("reason", "Outward action blocked.")))
        if re.search(r"\bgh\s+pr\s+create\b", command):
            return _decision("DENY", "PR creation is disabled in the Agent OS fit test.")
        if not _is_known_safe_shell_command(command, guard_path, task_root):
            if state.get("agent_os_critical_lane") is True:
                return _decision(
                    "DENY", "Critical-lane shell work is blocked pending Phase B approval."
                )
            return cast(
                PolicyResponse,
                {
                    "result": "ASK",
                    "reason": f"Approve this exact shell command before it runs: {command}",
                    "state_updates": _invalidate_write_evidence(),
                },
            )
        if state.get("agent_os_staged_paths") is not None and not any(
            _is_staged_inventory_command(segment) for segment in split_command_segments(command)
        ):
            return {
                "result": "ALLOW",
                "reason": "A shell command ran after staged inventory; inventory is stale.",
                "state_updates": [{"key": "agent_os_staged_paths", "action": "delete"}],
            }
        return None

    return _evaluate


POLICY_REGISTRY: list[_Json] = [
    {
        "handler": "sifututor_agent_os_omnigent.policy.agent_os_workflow_guard",
        "kind": "factory",
        "name": "Agent OS Workflow Guard",
        "description": (
            "Enforces exact-file approval, secret/root boundaries, safe staging, commit evidence, "
            "critical-lane diagnosis-only behavior, and honest verification claims."
        ),
        "params_schema": {
            "type": "object",
            "properties": {
                "allowed_roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Explicit project and worktree roots the task may access.",
                },
                "guard_command_path": {
                    "type": "string",
                    "description": "Exact pre-commit guard executable inside an allowed root.",
                },
                "workspace_root": {
                    "type": "string",
                    "description": "Exact task workspace used to resolve relative paths.",
                },
                "shell_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Shell tool names whose command strings are evaluated.",
                },
            },
            "required": ["allowed_roots", "guard_command_path"],
        },
    }
]
