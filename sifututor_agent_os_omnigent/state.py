"""Attach Agent OS proven-state evidence to the current Omnigent chat."""

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

_MAX_STATE = 180
_MAX_EVIDENCE = 240


def _bounded_value(value: str, *, name: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > limit:
        raise ValueError(f"{name} must be {limit} characters or fewer")
    return normalized


def set_current_session_proven_state(
    *,
    state: str,
    evidence: str,
    session_id: str,
    server_url: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, str]:
    """Attach a bounded state/evidence summary to only the current chat."""
    current_session_id = session_id.strip()
    if not current_session_id:
        raise ValueError("current Omnigent session identity is unavailable")
    proven_state = _bounded_value(state, name="state", limit=_MAX_STATE)
    proven_evidence = _bounded_value(evidence, name="evidence", limit=_MAX_EVIDENCE)
    labels = {
        "agent_os.proven_state": proven_state,
        "agent_os.proven_state_evidence": proven_evidence,
        "agent_os.proven_state_source": "orchestrator",
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
        raise RuntimeError("Omnigent did not confirm the Agent OS proven state")
    return labels


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by the orchestrator at meaningful state changes."""
    parser = argparse.ArgumentParser(
        description="Show the strongest state currently proven for this Omnigent task."
    )
    parser.add_argument("--state", required=True, help="Strongest honestly proven state")
    parser.add_argument(
        "--evidence",
        required=True,
        help="Short fresh evidence supporting that state",
    )
    args = parser.parse_args(argv)

    session_id = _first_environment_value(_SESSION_ID_ENV)
    server_url = _first_environment_value(_SERVER_URL_ENV)
    if not session_id:
        parser.error("this command must run inside an Omnigent task session")
    if not server_url:
        parser.error("the Omnigent server address is unavailable")

    labels = set_current_session_proven_state(
        state=args.state,
        evidence=args.evidence,
        session_id=session_id,
        server_url=server_url,
    )
    print(
        json.dumps(
            {
                "updated": True,
                "state": labels["agent_os.proven_state"],
                "evidence": labels["agent_os.proven_state_evidence"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
