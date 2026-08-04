"""Non-secret coordinates exposed to a native task session."""

from __future__ import annotations


def native_session_env(session_id: str, server_url: str) -> dict[str, str]:
    """Return the current Omnigent chat identity and API base for integrations."""
    current_session = session_id.strip()
    current_server = server_url.strip().rstrip("/")
    if not current_session:
        raise ValueError("native session id must not be empty")
    if not current_server:
        raise ValueError("native server URL must not be empty")
    return {
        "OMNIGENT_SESSION_ID": current_session,
        "OMNIGENT_SERVER_URL": current_server,
    }


__all__ = ["native_session_env"]
