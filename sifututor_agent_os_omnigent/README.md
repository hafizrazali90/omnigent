# Sifututor Agent OS adapter for Omnigent

This package keeps Sifututor-specific policy outside the Omnigent framework
wheel. Install this adapter into the same Python environment as Omnigent, then
load `sifututor_agent_os_omnigent.policy` as an external policy module.

The adapter supports Omnigent 0.7 and 0.8. It prefers the public policy-shell
helpers when available and retains the 0.7 fallback until that compatibility
window is intentionally retired.

The full Agent OS workflow guard replaces Omnigent's default
`omnigent.policies.builtins.safety.ask_on_os_tools` policy. Do not enable both:
the default policy asks about every OS read, while the Agent OS guard permits
safe reads and requests approval only at meaningful write or risk boundaries.

Configure the server policy with Omnigent's native factory form so the adapter
receives its required roots and guard path:

```yaml
policy_modules:
  - sifututor_agent_os_omnigent.policy

extension_router_modules:
  - sifututor_agent_os_omnigent.continuity

agent_os_continuity:
  workspace_root: /absolute/path/to/Sifututor

policies:
  agent_os_workflow_guard:
    type: function
    function:
      path: sifututor_agent_os_omnigent.policy.agent_os_workflow_guard
      arguments:
        allowed_roots:
          - /absolute/path/to/Sifututor
          - /absolute/path/to/Sifututor-worktrees
        workspace_root: /absolute/path/to/Sifututor
        guard_command_path: /absolute/path/to/Sifututor/scripts/agent-checks/pre-commit-guard.sh
```

The continuity route is read-only. Each Agent OS chat labels itself with its
source-relative Session Map (for example,
`agent_os.session_map=.agent-os/session-maps/current-work.md`). The route
revalidates that pointer against the configured Session Map glob and rereads
that exact map plus unresolved Mission Ledger items on every request. It never
guesses from the most recently edited map, so parallel tasks cannot inherit one
another's context. The existing Markdown files remain the owners; the adapter
does not create or update task state.

After the task router has created or selected one exact Session Map, link it to
the current native chat with:

```bash
agent-os-link-continuity \
  .agent-os/session-maps/current-work.md \
  --workspace-root /absolute/path/to/Sifututor
```

Omnigent supplies the current chat id and server address to native Claude and
Codex sessions as non-secret environment coordinates. The helper accepts no
session-id argument, validates the map before making any request, and changes
only the current chat's `agent_os.session_map` label. Invalid, missing,
absolute, traversal, and outside-root paths fail before any chat metadata is
written. Authenticated multi-user servers still enforce their normal session
edit permission on the metadata request.

After the task router has identified the project and workflow, make that
understanding visible in the same native chat before delegating work:

```bash
agent-os-set-understanding \
  --project ripple-suite \
  --workflow review \
  --finish-line "PR opened"
```

If choosing the wrong route could materially change the work, preserve the
uncertainty and one short question instead of silently guessing:

```bash
agent-os-set-understanding \
  --project umbrella \
  --workflow triage \
  --finish-line "Route confirmed" \
  --question "Which product should this change?"
```

The helper changes only the current chat's bounded `agent_os.*` understanding
labels. The Worker Sidebar shows the project, workflow, and practical finish
line and lets the current user correct them without changing the original
request or creating another task record.

Do not use the legacy top-level `handler` plus `factory_params` spelling for
this server-wide policy. Omnigent 0.8 accepts that spelling but currently drops
the factory arguments when it builds the live policy engine.
