"""Native session integration coordinates."""

import pytest

from omnigent.native_session_env import native_session_env


def test_native_session_env_exposes_only_non_secret_current_chat_coordinates() -> None:
    assert native_session_env(" conv_current ", "http://127.0.0.1:6767/") == {
        "OMNIGENT_SESSION_ID": "conv_current",
        "OMNIGENT_SERVER_URL": "http://127.0.0.1:6767",
    }


@pytest.mark.parametrize(
    ("session_id", "server_url"),
    [
        ("", "http://127.0.0.1:6767"),
        ("conv_current", ""),
    ],
)
def test_native_session_env_rejects_missing_coordinates(
    session_id: str,
    server_url: str,
) -> None:
    with pytest.raises(ValueError):
        native_session_env(session_id, server_url)
