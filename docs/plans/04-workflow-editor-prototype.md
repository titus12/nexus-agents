# Workflow Editor Prototype Plan

## Goal
Prototype the Workflow Template Library and a ComfyUI-style editor for composing agent roles.

## Files
- `design/pages/workflows.html`
- `design/js/workflow-canvas.js`
- `design/overlays/drawer-node-config.html`
- `design/overlays/drawer-workflow-run.html`

## Implementation
- Workflows are Template Library entries with the same copy-and-sync model as Agents, Rules, and Skills.
- Project workflows can be copied from templates, changed locally, and manually synced later through template diff.
- Workflow metadata should support package or manifest management so the node graph, node config, input/output mappings, and prompt context sync together.
- Use pure HTML, CSS, SVG, and JavaScript to simulate a node canvas.
- Node categories cover AI workflow needs: action/agent, control flow, condition, data transform, human approval, event trigger, and decorator/context nodes.
- Edges show data flow and labels for field mappings.
- Default Workflows page shows a workflow library/list and preview; Node Palette appears only when creating or editing a workflow.
- Clicking a node opens a config drawer with input schema, output schema, bindings, and mock prompt context.
- Simulate a run by changing node states and opening a run detail drawer inside Workflows.
- V1 does not support promoting project workflow changes back into the global template library.

## Acceptance Criteria
- The workflow page renders a library/list and node canvas preview.
- Creating or editing a workflow reveals the Node Palette and node configuration affordances.
- Selecting nodes highlights them and shows configuration.
- Simulated run updates status chips and run timeline inside the Workflows experience.
- Workflow cards can show template version, project usage, and sync status.
- Project workflow copies can display origin template id, base version, base hash, and local version.
