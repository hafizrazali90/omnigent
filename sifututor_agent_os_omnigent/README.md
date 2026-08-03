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

Do not use the legacy top-level `handler` plus `factory_params` spelling for
this server-wide policy. Omnigent 0.8 accepts that spelling but currently drops
the factory arguments when it builds the live policy engine.
