"""External-module ownership fixtures for the Sifututor Agent OS policy."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from omnigent.policies.builtins import BUILTIN_POLICY_MODULES
from omnigent.policies.registry import get_entry, load_registry

_HANDLER = "sifututor_agent_os_omnigent.policy.agent_os_workflow_guard"
_MODULE = "sifututor_agent_os_omnigent.policy"


def test_agent_os_guard_is_not_an_omnigent_builtin() -> None:
    assert "omnigent.policies.builtins.agent_os" not in BUILTIN_POLICY_MODULES


def test_agent_os_guard_loads_through_external_policy_modules() -> None:
    load_registry(extra_modules=[_MODULE])

    entry = get_entry(_HANDLER)

    assert entry is not None
    assert entry.name == "Agent OS Workflow Guard"
    assert entry.kind == "factory"


def test_agent_os_guard_is_absent_without_external_policy_module() -> None:
    load_registry()

    assert get_entry(_HANDLER) is None


def test_external_adapter_builds_as_an_independent_installable_wheel(tmp_path: Path) -> None:
    """The Sifututor adapter must install without joining Omnigent's own wheel."""
    adapter_dir = Path(__file__).parents[2] / "sifututor_agent_os_omnigent"
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(
        [
            uv,
            "build",
            "--wheel",
            "--out-dir",
            str(tmp_path),
            str(adapter_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    wheels = list(tmp_path.glob("sifututor_agent_os_omnigent-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    assert "sifututor_agent_os_omnigent/__init__.py" in names
    assert "sifututor_agent_os_omnigent/continuity.py" in names
    assert "sifututor_agent_os_omnigent/linking.py" in names
    assert "sifututor_agent_os_omnigent/policy.py" in names
    assert "sifututor_agent_os_omnigent/state.py" in names
    assert "sifututor_agent_os_omnigent/understanding.py" in names
    entry_points = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
    with zipfile.ZipFile(wheels[0]) as archive:
        entry_point_text = archive.read(entry_points).decode("utf-8")
    assert (
        "agent-os-link-continuity = sifututor_agent_os_omnigent.linking:main" in entry_point_text
    )
    assert (
        "agent-os-set-understanding = sifututor_agent_os_omnigent.understanding:main"
        in entry_point_text
    )
    assert "agent-os-set-proven-state = sifututor_agent_os_omnigent.state:main" in entry_point_text

    env = {**os.environ, "PYTHONPATH": str(wheels[0])}
    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            "from sifututor_agent_os_omnigent.policy import POLICY_REGISTRY; "
            "print(POLICY_REGISTRY[0]['handler'])",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == _HANDLER


def test_adapter_does_not_require_the_unreleased_omnigent_shell_seam() -> None:
    """The declared 0.7-0.8 compatibility must stay on published policy seams."""
    adapter_dir = Path(__file__).parents[2] / "sifututor_agent_os_omnigent"
    policy_source = (adapter_dir / "policy.py").read_text(encoding="utf-8")

    assert "from omnigent.policies.shell import" not in policy_source


def test_adapter_declares_blanket_os_tool_approval_incompatible() -> None:
    """The full guard replaces the old ask-on-every-read policy."""
    from sifututor_agent_os_omnigent import INCOMPATIBLE_DEFAULT_POLICY_HANDLERS

    assert "omnigent.policies.builtins.safety.ask_on_os_tools" in (
        INCOMPATIBLE_DEFAULT_POLICY_HANDLERS
    )
