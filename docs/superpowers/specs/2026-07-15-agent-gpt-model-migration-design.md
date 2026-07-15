# Agent GPT Model Migration Design

## Goal

Make every project Agent and Codex skill sub-agent default model a Codex GPT model. Keep the non-GPT router routes available, but remove their use from Agent role configuration and role-facing documentation.

## Scope

The migration updates four synchronized configuration surfaces:

1. `templates/agents/codex/*.toml` is the executable Codex Agent projection.
2. `templates/agents/claude/*.md` remains the paired Agent source shown in the template library, so its `model:` metadata and model references must not advertise non-GPT roles.
3. `internal/catalog/catalog.go` provides the template-library `ModelTier` and generated Codex projection metadata. It must match the executable templates.
4. `templates/skills/codex/**/agents/openai.yaml` sets default sub-agent models and must not retain DeepSeek defaults.

`internal/codexrouter/router.go` is explicitly out of scope. Its DeepSeek, GLM, and Claude routes are global proxy capabilities, not Agent role assignment.

## Model Policy

Use the current Codex GPT tiers as follows:

| Role class | Model | Rationale |
| --- | --- | --- |
| Architecture, high-risk evaluation, logic/security review, asset safety | `gpt-5.6-terra` | High-capability reasoning and coding tier, selected as the replacement for former GPT-5.5 / DeepSeek Pro premium roles. |
| Standard implementation, debugger, reviewer, regression evaluation | `gpt-5.4` | Stable general-purpose implementation and review tier. |
| Fast edits, lookup, gatekeeping, workflow first-pass evaluation | `gpt-5.6-luna` | Fast and lower-cost tier, selected as the replacement for former GPT-5.4-mini / DeepSeek Flash roles. |

## Role Mapping

| Role | Current non-GPT assignment | New GPT assignment |
| --- | --- | --- |
| `go-gatekeeper` | `deepseek-v4-flash` | `gpt-5.6-luna` |
| `go-librarian` | `deepseek-v4-flash` | `gpt-5.6-luna` |
| `go-quick` | `deepseek-v4-flash` | `gpt-5.6-luna` |
| `go-workflow-evaluator` | `deepseek-v4-flash` | `gpt-5.6-luna` |
| `unity-workflow-evaluator` | `deepseek-v4-flash` | `gpt-5.6-luna` |
| `go-worker` | `deepseek-v4-pro` | `gpt-5.4` |
| `go-reviewer-perf` | `deepseek-v4-pro` | `gpt-5.4` |
| `unity-debugger` | `deepseek-v4-pro` | `gpt-5.4` |
| `unity-bugfix-reviewer` | `deepseek-v4-pro` | `gpt-5.4` |
| `unity-logic-reviewer` | `deepseek-v4-pro` | `gpt-5.4` |
| `unity-regression-evaluator` | `deepseek-v4-pro` | `gpt-5.4` |
| `go-model-arbiter` | `deepseek-v4-pro` | `gpt-5.6-terra` |
| `go-reviewer-logic` | `deepseek-v4-pro` | `gpt-5.6-terra` |
| `go-reviewer-security` | `deepseek-v4-pro` | `gpt-5.6-terra` |
| `unity-asset-safety-evaluator` | `deepseek-v4-pro` | `gpt-5.6-terra` |

Existing GPT assignments are normalized to this policy where applicable: premium roles use Terra, standard roles use GPT-5.4, and low-cost roles use Luna.

## Skill Sub-Agent Defaults

The Unity Codex workflows `wf-unity-bugfix`, `wf-unity-logic-mod`, `wf-unity-ui-feature`, and `wf-unity-ui-quick` use `gpt-5.4` as their sub-agent default. Their workflows perform implementation and verification rather than fast first-pass triage.

## Verification

1. Run the catalog and HTTP API Go tests that assert Agent template metadata and Codex projections.
2. Search Agent templates and Codex skill `openai.yaml` files for non-GPT default assignments.
3. Confirm any remaining DeepSeek, GLM, or Claude references are confined to the intentionally preserved model-router functionality or unrelated historical documentation.
