# Zhongshu Analyst Contract Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the canonical Zhongshu requirement contract, including `acceptance_signal`, authoritative for Analyst evidence packets while preserving strict ID validation.

**Architecture:** Add one deterministic binding helper at the Zhongshu parallel boundary. It validates that the Analyst returned exactly the canonical requirement IDs, replaces only mutable/rephrased requirement objects with a deep copy of the canonical contract, and leaves evidence fields untouched. Apply the helper both before parallel-worker validation and again during fan-in so recovered/direct paths share the same invariant. Strengthen the generated Analyst prompt and add focused local regression tests.

**Tech Stack:** Python 3, `unittest`, existing `zhongshu_parallel.py` validators, `OrchestratorApp._validate_parallel_worker_reply`, and the existing runtime prompt builder.

---

### Task 1: Add regression tests for canonical contract binding

**Files:**
- Modify: `cmd/test_zhongshu_evidence_routing.py`
- Test: `cmd/test_zhongshu_evidence_routing.py`

- [ ] **Step 1: Add imports and a canonical contract fixture**

Add `merge_analyst_evidence` to the existing `orchestrator.zhongshu_parallel` imports and add this fixture inside the test class:

```python
    @staticmethod
    def _canonical_contract():
        return [{
            "requirement_id": "REQ-1",
            "statement": "Review the workflow",
            "source": "user",
            "priority": "must",
            "scope": "in",
            "kind": "task",
            "acceptance_signal": "The review identifies the remaining optimization space",
        }]
```

- [ ] **Step 2: Add the shortened-acceptance regression**

Add a test that passes valid IDs but a shortened `acceptance_signal` to the merge function and verifies that the canonical signal is restored while the Analyst evidence remains:

```python
    def test_merge_rebinds_changed_acceptance_signal_without_dropping_evidence(self):
        canonical = self._canonical_contract()
        worker = {
            "worker_id": "analyst-1",
            "revision_id": "revision-1",
            "action": "EVIDENCE_PACKET_READY",
            "phase": "ZHONGSHU",
            "requirements": [{**canonical[0], "acceptance_signal": "The review is complete"}],
            "task_proposals": [],
            "candidate_items": [],
            "candidate_groups": [],
            "evidence_updates": [{
                "evidence_id": "ev-1",
                "requirement_id": "REQ-1",
                "decision_relevance": "acceptance",
                "source": "cmd/orchestrator/app.py:1",
                "conclusion": "The canonical acceptance condition is needed by Solver.",
            }],
        }
        merged = merge_analyst_evidence(
            "task-1", "revision-1", [worker], canonical_requirements=canonical
        )
        self.assertEqual(
            merged["plan"]["requirements"],
            canonical,
        )
        self.assertEqual(
            merged["plan"]["evidence_updates"][0]["evidence_id"],
            "ev-1",
        )
```

- [ ] **Step 3: Add ID-integrity and prompt-contract regressions**

Add tests that missing/extra IDs still raise `ValueError`, and that the generated Analyst rules explicitly mention every immutable contract field:

```python
    def test_merge_rejects_missing_or_extra_requirement_ids(self):
        canonical = self._canonical_contract()
        base = {
            "worker_id": "analyst-1",
            "revision_id": "revision-1",
            "action": "EVIDENCE_PACKET_READY",
            "phase": "ZHONGSHU",
            "requirements": [],
            "task_proposals": [],
            "candidate_items": [],
            "candidate_groups": [],
            "evidence_updates": [],
        }
        with self.assertRaisesRegex(ValueError, "REQUIREMENT_CONTRACT_ID"):
            merge_analyst_evidence(
                "task-1", "revision-1", [base], canonical_requirements=canonical
            )
        extra = {**base, "requirements": [
            canonical[0],
            {**canonical[0], "requirement_id": "REQ-2"},
        ]}
        with self.assertRaisesRegex(ValueError, "REQUIREMENT_CONTRACT_ID"):
            merge_analyst_evidence(
                "task-1", "revision-1", [extra], canonical_requirements=canonical
            )

    def test_analyst_prompt_requires_verbatim_immutable_contract_fields(self):
        ctx = StateContext(
            task_id="task-1",
            raw_request="Review the workflow",
            workflow_state="ZHONGSHU_ANALYST",
            current_phase="ZHONGSHU",
            current_role="review-analyst",
        )
        prompt = json.loads(ZhongshuAnalystState().request(ctx).prompt)
        rules = " ".join(prompt["working_rules"])
        for field in (
            "requirement_id", "statement", "source", "priority",
            "scope", "kind", "acceptance_signal",
        ):
            self.assertIn(field, rules)
```

- [ ] **Step 4: Run only the focused test file**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_zhongshu_evidence_routing.py -v"`

Expected: the new tests fail because the binding helper and strengthened prompt rule do not exist yet; no live service or Feishu adapter is used.

### Task 2: Implement deterministic requirement binding

**Files:**
- Modify: `cmd/orchestrator/zhongshu_parallel.py:377-434, 501-590`
- Test: `cmd/test_zhongshu_evidence_routing.py`

- [ ] **Step 1: Define the immutable field set and binding helper**

Place the following immediately before `validate_zhongshu_evidence_packet`:

```python
ZHONGSHU_REQUIREMENT_CONTRACT_FIELDS = (
    "requirement_id", "statement", "source", "priority",
    "scope", "kind", "acceptance_signal",
)


def bind_zhongshu_requirement_contract(
    payload: dict[str, Any],
    canonical_requirements: list[dict[str, Any]] | None,
    *,
    worker_id: str = "",
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Bind immutable requirement data without changing Analyst evidence.

    IDs are the integrity boundary: they must match exactly and cannot be
    repaired. Once IDs are valid, the canonical requirement objects are the
    authoritative copy, so model paraphrases cannot lose acceptance criteria.
    """
    bound = copy.deepcopy(payload)
    if not canonical_requirements:
        return bound, ()
    requirements = bound.get("requirements")
    if not isinstance(requirements, list):
        return bound, ()
    expected_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in canonical_requirements
        if isinstance(item, dict)
    ]
    actual_ids = [
        str(item.get("requirement_id") or "").strip()
        for item in requirements
        if isinstance(item, dict)
    ]
    if (
        len(expected_ids) != len(set(expected_ids))
        or len(actual_ids) != len(requirements)
        or len(actual_ids) != len(set(actual_ids))
        or set(actual_ids) != set(expected_ids)
    ):
        raise ValueError(
            "ZHONGSHU_EVIDENCE_PACKET_REQUIREMENT_CONTRACT_ID_MISMATCH:"
            + (worker_id or "unknown-worker")
        )
    changed: dict[str, list[str]] = {}
    expected_by_id = {
        str(item["requirement_id"]): item for item in canonical_requirements
    }
    actual_by_id = {str(item["requirement_id"]): item for item in requirements}
    for requirement_id, reference in expected_by_id.items():
        candidate = actual_by_id[requirement_id]
        fields = [
            field for field in ZHONGSHU_REQUIREMENT_CONTRACT_FIELDS
            if candidate.get(field) != reference.get(field)
        ]
        if fields:
            changed[requirement_id] = fields
    if changed:
        bound["requirements"] = copy.deepcopy(canonical_requirements)
        logger.warning(
            "ZHONGSHU_ANALYST_REQUIREMENT_CONTRACT_REBOUND worker_id=%s "
            "requirement_ids=%s changed_fields=%s",
            worker_id or "unknown-worker",
            ",".join(sorted(changed)),
            json.dumps(changed, ensure_ascii=False, sort_keys=True),
        )
    else:
        bound["requirements"] = [
            copy.deepcopy(expected_by_id[requirement_id])
            for requirement_id in expected_ids
        ]
    return bound, tuple(
        f"{requirement_id}:{','.join(fields)}"
        for requirement_id, fields in sorted(changed.items())
    )
```

- [ ] **Step 2: Bind before validating each worker during fan-in**

In `merge_analyst_evidence`, replace the current body assignment and direct validation with:

```python
        body, _binding_notes = bind_zhongshu_requirement_contract(
            _analyst_worker_body(result),
            canonical_requirements,
            worker_id=worker_id,
        )
        reason = validate_zhongshu_evidence_packet(
            body,
            canonical_requirements,
            max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER,
            max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER,
            require_decision_relevance=True,
        )
```

Keep all existing evidence merge logic unchanged. The binding must affect only `body["requirements"]`.

- [ ] **Step 3: Run the focused tests and verify ID failures remain hard failures**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_zhongshu_evidence_routing.py -v"`

Expected: the shortened acceptance signal is rebound to the canonical value, evidence remains present, and missing/extra IDs are rejected.

### Task 3: Apply binding at the live parallel validation boundary

**Files:**
- Modify: `cmd/orchestrator/app.py:56-65, 2825-2838`
- Test: `cmd/test_zhongshu_evidence_routing.py`

- [ ] **Step 1: Import the binding helper**

Add `bind_zhongshu_requirement_contract` to the existing `.zhongshu_parallel` import block:

```python
    bind_zhongshu_requirement_contract,
```

- [ ] **Step 2: Bind `EVIDENCE_PACKET_READY` payloads in place before validation**

Replace the `EVIDENCE_PACKET_READY` branch body with the following shape, preserving the existing canonical requirement lookup and validator limits:

```python
            if payload.get("action") == "EVIDENCE_PACKET_READY":
                canonical_requirements = (
                    worker.request.context.get("requirement_contract")
                    if isinstance(worker.request.context.get("requirement_contract"), list)
                    else None
                )
                bound_payload, _binding_notes = bind_zhongshu_requirement_contract(
                    payload,
                    canonical_requirements,
                    worker_id=worker.worker_id,
                )
                payload.clear()
                payload.update(bound_payload)
                reason = validate_zhongshu_evidence_packet(
                    payload,
                    canonical_requirements=canonical_requirements,
                    max_evidence_updates=ANALYST_MAX_EVIDENCE_UPDATES_PER_WORKER,
                    max_evidence_requests=ANALYST_MAX_EVIDENCE_REQUESTS_PER_WORKER,
                    require_decision_relevance=True,
                )
                if reason:
                    raise ValueError(reason)
                return
```

Do not apply this binding to `EVIDENCE_SUPPLEMENT_READY`; supplement payloads are scoped evidence updates and do not replace the authoritative contract.

- [ ] **Step 3: Run the focused tests without starting the service**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_zhongshu_evidence_routing.py -v"`

Expected: all focused tests pass, and no network adapter or notification path is invoked.

### Task 4: Strengthen the generated Analyst prompt

**Files:**
- Modify: `cmd/orchestrator/app.py:2382-2390`
- Test: `cmd/test_zhongshu_evidence_routing.py`

- [ ] **Step 1: Replace the incomplete preservation rule**

Replace:

```python
"Preserve requirement_ids and statements from requirement_contract; do not redefine requirements.",
```

with:

```python
"Copy every requirement object from requirement_contract verbatim and in the same order, including requirement_id, statement, source, priority, scope, kind, and acceptance_signal; never paraphrase, omit, invent, reorder, or redefine these immutable fields. The orchestrator owns this contract; you only add evidence.",
```

- [ ] **Step 2: Keep the runtime Skill boundary aligned**

Verify `docs/multi/runtime/zhongshu-analyst-skill.md` continues to state that evidence-mode Analyst responses must copy `requirement_id`, `statement`, `source`, `priority`, `scope`, `kind`, and `acceptance_signal`. Do not remove its existing prohibition on creating tasks or defining final acceptance criteria; `acceptance_signal` here is the user/canonical requirement contract, not an Analyst-created final task acceptance plan.

- [ ] **Step 3: Run the prompt assertion**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_zhongshu_evidence_routing.py -k prompt -v"`

Expected: the prompt includes all seven immutable field names.

### Task 5: Self-review and local verification

**Files:**
- Review: `cmd/orchestrator/zhongshu_parallel.py`
- Review: `cmd/orchestrator/app.py`
- Review: `docs/multi/runtime/zhongshu-analyst-skill.md`

- [ ] **Step 1: Check the diff for scope and syntax**

Run: `rtk powershell -NoProfile -Command "git diff --check; python -m py_compile cmd/orchestrator/zhongshu_parallel.py cmd/orchestrator/app.py cmd/test_zhongshu_evidence_routing.py"`

Expected: no whitespace errors and no Python syntax errors.

- [ ] **Step 2: Run the focused regression suite once**

Run: `rtk powershell -NoProfile -Command "python -m unittest cmd/test_zhongshu_evidence_routing.py -v"`

Expected: all tests pass; the command uses only local fake adapters and sends no Feishu notification.

- [ ] **Step 3: Inspect the final diff**

Run: `rtk powershell -NoProfile -Command "git diff --stat; git diff -- cmd/orchestrator/zhongshu_parallel.py cmd/orchestrator/app.py cmd/test_zhongshu_evidence_routing.py"`

Expected: only the deterministic contract binding, explicit prompt rule, and focused tests are changed; no FSM transition, quorum threshold, timeout, or notification behavior changes.

- [ ] **Step 4: Commit the implementation**

```powershell
git add cmd/orchestrator/zhongshu_parallel.py cmd/orchestrator/app.py cmd/test_zhongshu_evidence_routing.py
git commit -m "fix: bind Zhongshu analyst requirement contract"
```
