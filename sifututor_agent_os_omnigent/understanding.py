"""Attach Agent OS task understanding to the current Omnigent chat."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from urllib.parse import quote

import httpx

from .linking import (
    _SERVER_URL_ENV,
    _SESSION_ID_ENV,
    _first_environment_value,
    _validated_server_url,
)

_MAX_ROUTE_VALUE = 120
_MAX_QUESTION = 240


def _bounded_value(value: str, *, name: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > limit:
        raise ValueError(f"{name} must be {limit} characters or fewer")
    return normalized


def set_current_session_understanding(
    *,
    project: str,
    workflow: str,
    question: str,
    session_id: str,
    server_url: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, str]:
    """Attach a bounded, visible route summary to only the current chat."""
    current_session_id = session_id.strip()
    if not current_session_id:
        raise ValueError("current Omnigent session identity is unavailable")
    route_project = _bounded_value(project, name="project", limit=_MAX_ROUTE_VALUE)
    route_workflow = _bounded_value(workflow, name="workflow", limit=_MAX_ROUTE_VALUE)
    route_question = " ".join(question.split())
    if len(route_question) > _MAX_QUESTION:
        raise ValueError(f"question must be {_MAX_QUESTION} characters or fewer")
    labels = {
        "agent_os.project": route_project,
        "agent_os.workflow": route_workflow,
        "agent_os.route_status": (
            "needs-clarification" if route_question else "understood"
        ),
        "agent_os.route_question": route_question,
        "agent_os.route_source": "orchestrator",
    }
    base_url = _validated_server_url(server_url)
    with httpx.Client(base_url=base_url, transport=transport, timeout=10.0) as client:
        response = client.patch(
            f"/v1/sessions/{quote(current_session_id, safe='')}",
            json={"labels": labels},
        )
    response.raise_for_status()
    payload = response.json()
    saved = payload.get("labels") if isinstance(payload, dict) else None
    confirmed = isinstance(saved, dict) and all(
        saved.get(key) == value for key, value in labels.items()
    )
    if not confirmed:
        raise RuntimeError("Omnigent did not confirm the Agent OS task understanding")
    return labels


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by the Agent OS router inside a native chat."""
    parser = argparse.ArgumentParser(
        description="Show what Agent OS understands about this Omnigent task."
    )
    parser.add_argument("--project", required=True, help="Detected project or workspace")
    parser.add_argument("--workflow", required=True, help="Detected Agent OS workflow")
    parser.add_argument(
        "--question",
        default="",
        help="One clarification question when the route is materially ambiguous",
    )
    args = parser.parse_args(argv)

    session_id = _first_environment_value(_SESSION_ID_ENV)
    server_url = _first_environment_value(_SERVER_URL_ENV)
    if not session_id:
        parser.error("this command must run inside an Omnigent task session")
    if not server_url:
        parser.error("the Omnigent server address is unavailable")

    labels = set_current_session_understanding(
        project=args.project,
        workflow=args.workflow,
        question=args.question,
        session_id=session_id,
        server_url=server_url,
    )
    print(
        json.dumps(
            {
                "updated": True,
                "project": labels["agent_os.project"],
                "workflow": labels["agent_os.workflow"],
                "status": labels["agent_os.route_status"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
