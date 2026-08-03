"""End-to-end approval and evidence bridge tests for the Agent OS policy."""

from __future__ import annotations

import json
from typing import Any

import pytest

from omnigent.errors import ElicitationDeclinedError
from omnigent.policies.function import FunctionPolicy, resolve_function_policy
from omnigent.policies.types import EvaluationContext
from omnigent.runtime.policies import _await_elicitation
from omnigent.runtime.policies.engine import PolicyEngine
from omnigent.spec import parse_default_policies
from omnigent.spec.types import FunctionPolicySpec, FunctionRef, Phase, PhaseSelector, PolicyAction
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from sifututor_agent_os_omnigent.policy import agent_os_workflow_guard

_ROOT = "/workspace/project"
_TARGET = f"{_ROOT}/src/app.py"
_GUARD = f"{_ROOT}/scripts/agent-checks/pre-commit-guard.sh"


def _policy() -> FunctionPolicy:
    evaluator = agent_os_workflow_guard(
        allowed_roots=[_ROOT],
        guard_command_path=_GUARD,
    )
    spec = FunctionPolicySpec(
        name="agent_os_workflow_guard",
        on=[
            PhaseSelector(phase=Phase.REQUEST),
            PhaseSelector(phase=Phase.RESPONSE),
            PhaseSelector(phase=Phase.TOOL_CALL),
            PhaseSelector(phase=Phase.TOOL_RESULT),
        ],
        function=FunctionRef(path="test.not.used"),
    )
    return FunctionPolicy(spec, evaluator)


def _engine(store: SqlAlchemyConversationStore) -> PolicyEngine:
    conversation = store.create_conversation()
    return PolicyEngine(
        policies=[_policy()],
        label_defs={},
        ask_timeout=30,
        conversation_id=conversation.id,
        initial_labels={},
        conversation_store=store,
    )


def _edit_context(path: str = _TARGET) -> EvaluationContext:
    return EvaluationContext(
        phase=Phase.TOOL_CALL,
        content={"name": "Edit", "arguments": {"file_path": path}},
        tool_name="Edit",
    )


@pytest.mark.asyncio
async def test_server_config_native_factory_form_builds_a_live_guard() -> None:
    specs = parse_default_policies(
        {
            "agent_os_workflow_guard": {
                "type": "function",
                "function": {
                    "path": ("sifututor_agent_os_omnigent.policy.agent_os_workflow_guard"),
                    "arguments": {
                        "allowed_roots": [_ROOT],
                        "workspace_root": _ROOT,
                        "guard_command_path": _GUARD,
                    },
                },
            }
        }
    )

    policy = resolve_function_policy(specs[0])
    result = await policy.evaluate(
        EvaluationContext(
            phase=Phase.REQUEST,
            content={"user_content": "Summarize the current state read-only."},
        ),
        {},
    )

    assert result.action == PolicyAction.ALLOW


async def _resolve(engine: PolicyEngine, result: Any, verdict: str) -> bool:
    async def _park(_elicitation_id: str, _timeout_s: int) -> str:
        return json.dumps({"action": verdict})

    try:
        return await _await_elicitation(
            task_id="task_1",
            root_task_id="task_1",
            result=result,
            phase=Phase.TOOL_CALL,
            content_preview=_TARGET,
            policy_engine=engine,
            register=lambda *_args: None,
            emit=lambda _event: None,
            park=_park,
        )
    except ElicitationDeclinedError:
        return False


@pytest.mark.asyncio
async def test_accept_records_exact_paths_only_in_the_current_task(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    first_task = _engine(conversation_store)
    result = await first_task.evaluate(_edit_context())

    assert result.action == PolicyAction.ASK
    assert first_task.session_state == {}
    assert await _resolve(first_task, result, "accept") is True
    assert first_task.session_state["agent_os_approved_write_paths"] == [_TARGET]
    assert (await first_task.evaluate(_edit_context())).action == PolicyAction.ALLOW

    second_task = _engine(conversation_store)
    assert (await second_task.evaluate(_edit_context())).action == PolicyAction.ASK
    assert second_task.session_state == {}


@pytest.mark.asyncio
async def test_decline_leaves_no_approval_state(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    engine = _engine(conversation_store)
    result = await engine.evaluate(_edit_context())

    assert await _resolve(engine, result, "decline") is False
    assert engine.session_state == {}
    assert (await engine.evaluate(_edit_context())).action == PolicyAction.ASK


@pytest.mark.asyncio
async def test_observed_evidence_enables_commit_review_but_not_auto_commit(
    conversation_store: SqlAlchemyConversationStore,
) -> None:
    engine = _engine(conversation_store)
    approval = await engine.evaluate(_edit_context())
    assert await _resolve(engine, approval, "accept") is True

    observations = [
        (
            _GUARD,
            "pre-commit-guard: completed",
        ),
        ("pytest -q tests/test_app.py", "1 passed"),
        (
            "git diff --cached --name-only",
            _TARGET,
        ),
    ]
    for command, output in observations:
        result = await engine.evaluate(
            EvaluationContext(
                phase=Phase.TOOL_RESULT,
                content={"result": output, "succeeded": True},
                tool_name="Bash",
                request_data={"name": "Bash", "arguments": {"command": command}},
            )
        )
        assert result.action == PolicyAction.ALLOW

    commit = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL,
            content={"name": "Bash", "arguments": {"command": "git commit -m test"}},
            tool_name="Bash",
        )
    )
    assert commit.action == PolicyAction.ASK
    assert "local commit" in (commit.reason or "").lower()

    changed = await engine.evaluate(_edit_context())
    assert changed.action == PolicyAction.ALLOW
    assert "agent_os_guard_evidence" not in engine.session_state
    assert "agent_os_verify_evidence" not in engine.session_state
    assert "agent_os_staged_paths" not in engine.session_state

    stale_commit = await engine.evaluate(
        EvaluationContext(
            phase=Phase.TOOL_CALL,
            content={"name": "Bash", "arguments": {"command": "git commit -m stale"}},
            tool_name="Bash",
        )
    )
    assert stale_commit.action == PolicyAction.DENY
