"""Immutable aggregates that represent the workflow's durable domain state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace

from .findings import Finding
from .errors import FailureRecord, InvariantViolation


@dataclass(frozen=True)
class TaskIdentity:
    """Stable identity shared by every workflow event and effect."""

    task_id: str
    issue_id: str
    project: str
    request_id: str


@dataclass(frozen=True)
class RequestState:
    """Immutable request metadata and the artifact containing its payload."""

    raw_request: str = ""
    payload_ref: str | None = None
    project_type: str = "unknown"
    task_type: str = "review"


@dataclass(frozen=True)
class ZhongshuParallelLimits:
    enabled: bool = True
    analyst_max_workers: int = 3
    analyst_default_workers: int = 3
    # Critic width.  Every layer (context defaults, admission defaults, env
    # defaults) must agree on this cap; the values used to disagree
    # (context=6, admission/env=3), which made the effective width ambiguous.
    critic_max_workers: int = 4
    critic_default_workers: int = 4
    # A2/P3: revise contested items through parallel per-item Solver workers
    # (one binding per contested item) instead of one single-writer revision.
    # Default off; flipped by ZHONGSHU_ITEM_WORKFLOW_ENABLED.
    item_workflow_enabled: bool = False
    # 2026-09-26 group pipeline: "group" reviews one group per job with a
    # single group verdict (REVIEW_GROUP); "item" keeps the legacy per-item
    # task_review wave as the rollback path.
    review_unit: str = "group"
    # 2026-09-27 thin plan doc: after the freeze check approves, the run
    # pauses at a human gate carrying the plan-review document so the operator
    # can review before the hand-over to Menxia.  Flipped by
    # ZHONGSHU_PLAN_REVIEW_GATE=0 to keep the direct hand-over.
    plan_review_gate: bool = True
    # Fast track: the Zhongshu Critic approves the whole plan without a review
    # wave and the freeze check releases it immediately, so the run reaches
    # the Menxia group pipeline right after Analyst + Solver finish.
    # Default off; flipped by --fast-track / NEXUS_FAST_TRACK.
    fast_track: bool = False
    global_max_workers: int = 6
    per_task_max_workers: int = 6


@dataclass(frozen=True)
class MenxiaParallelLimits:
    # Menxia group pipeline is on by default; NEXUS_MENXIA_ENABLED=0 opts out.
    enabled: bool = True
    max_concurrent_groups: int = 1
    max_concurrent_items: int = 3
    failure_policy: str = "continue_and_block_group"


@dataclass(frozen=True)
class ParallelState:
    """Configuration and immutable pointers for node execution."""

    zhongshu: ZhongshuParallelLimits = field(default_factory=ZhongshuParallelLimits)
    menxia: MenxiaParallelLimits = field(default_factory=MenxiaParallelLimits)
    group_index: int | None = None
    item_index: int | None = None
    menxia_snapshot_ref: str | None = None
    active_node_run_id: str | None = None


@dataclass(frozen=True)
class ProgressState:
    """Position of the main linear FSM."""

    state: str
    sequence: int
    entered_at: str
    resume_state: str | None = None


@dataclass(frozen=True)
class DeliveryState:
    """Correlation and lifecycle data for the active external dispatch."""

    active_request_id: str | None = None
    dispatch_operation_id: str | None = None
    idempotency_key: str | None = None
    status: str = "IDLE"
    external_message_id: str | None = None
    phase: str = ""
    role: str = ""
    expected_agent_id: str = ""
    last_sent_at: str = ""
    attempt: int = 0
    last_result_ref: str | None = None


@dataclass(frozen=True)
class RecoveryState:
    """Retry and blocking information owned by workflow recovery."""

    retry_count: int = 0
    max_retries: int = 3
    recoverable: bool = True
    blocked_reason: str | None = None
    external_retry_count: int = 0
    reply_retry_count: int = 0
    max_external_retries: int = 3
    max_reply_retries: int = 3
    max_state_write_retries: int = 3
    timeout_retry_count: int = 0
    max_timeout_retries: int = 3
    timeout_phase: str | None = None
    timeout_role: str | None = None
    timeout_request_id: str | None = None
    last_failure: FailureRecord | None = None
    no_progress_count: int = 0
    max_no_progress: int = 3
    # Consecutive Critic rounds one finding may stay open before the loop is
    # escalated to a human (0 disables the escalation).
    max_stuck_finding_rounds: int = 3


@dataclass(frozen=True)
class ReviewState:
    """Immutable review ledger projection used by state decisions."""

    revision_id: str = ""
    active_group_id: str | None = None
    active_item_id: str | None = None
    findings: tuple[Finding, ...] = ()
    zhongshu_revision_round: int = 0
    max_zhongshu_revision_rounds: int = 8
    freeze_check_attempt: int = 0
    max_freeze_check_attempts: int = 2
    item_revision_round: int = 0
    max_item_revision_rounds: int = 5
    # Group-wide Menxia revision budget (shared document model): every
    # revision demand on any group consumes the group row's counter against
    # this cap before the group parks as ESCALATED.
    max_menxia_group_rounds: int = 5
    # Group-wide Zhongshu revision budget: every revision demand on any group
    # consumes the group row's counter against this cap before the group
    # parks as STALLED.
    max_zhongshu_group_rounds: int = 5
    last_reply_fingerprint: str = ""
    plan_ref: str | None = None
    plan_hash: str | None = None
    task_graph_ref: str | None = None
    task_items: tuple[ReviewTaskItem, ...] = ()
    task_groups: tuple[ReviewTaskGroup, ...] = ()
    completed_item_ids: tuple[str, ...] = ()
    task_review_ledger: tuple[ReviewTaskRecord, ...] = ()
    # Items the latest Solver revision was actually asked to fix, taken from its
    # bounded finding batch.  The per-item stall counter only advances for these,
    # so a task the Solver legitimately deferred to a later batch cannot be
    # escalated for rejections it never had a chance to address.
    attempted_item_ids: tuple[str, ...] = ()
    # Finding ids the latest Solver round was actually asked to resolve: the
    # batch echo for a revision, every active finding after a full re-plan.
    # ``None`` means no Solver round has folded yet, so the finding-level
    # stuck counter keeps its legacy count-every-round behaviour.  The stuck
    # counter only advances for these ids, mirroring the per-item stall rule:
    # a finding the batch never picked cannot be escalated for rounds it had
    # no chance to fix.
    attempted_finding_ids: tuple[str, ...] | None = None
    # The Analyst evidence packet (lens outputs merged per round).  It used to
    # travel only between Analyst and Solver inside the ``plan`` field, so the
    # task-review Critic judged every task with zero investigation context and
    # requested evidence the run already had.  The task-review bindings slice
    # this per item; ``None`` until the first lens round folds.
    evidence_packet: dict[str, object] | None = None
    # Per-item pipeline dispatch table (A2/P2).  Mirrors the ledger's verdicts
    # as pipeline phases; the ItemWorkflow executor consumes it to know which
    # stoves are open.  Empty until the first task-review round folds.
    item_workflows: tuple[ItemWorkflow, ...] = ()
    # Per-item Menxia pipeline records (stage barrier model).  Dispatch picks
    # every row whose stage matches the active MENXIA_* state, so several
    # items across groups can advance in one wave.  Empty until the first
    # Menxia dispatch seeds it from the task projection.
    menxia_items: tuple[MenxiaItemState, ...] = ()
    # Per-group Menxia pipeline records (shared document model).  One row per
    # review group; the group loops on one shared versioned plan document
    # instead of per-item waves.  Empty until the group dispatch seeds it.
    menxia_groups: tuple[MenxiaGroupState, ...] = ()
    # Per-group Zhongshu pipeline records.  One row per review group; the
    # group converges on one shared versioned requirements document instead
    # of per-item waves.  Empty until the group dispatch seeds it.
    zhongshu_groups: tuple[ZhongshuGroupState, ...] = ()
    requirements: tuple[dict[str, object], ...] = ()
    # The orchestrator-owned canonical task graph.  It is the base the Solver
    # patches with bounded ``changes`` and the source of the plan hash.
    plan: dict[str, object] | None = None
    # Full per-task review rows salvaged from a failed Critic wave (one worker
    # died after the others delivered usable verdicts).  A retry re-dispatches
    # only the tasks still missing; these rows stand in for the finished ones
    # so the retry's fan-in still sees their verdicts and findings.  Each row
    # stays valid only while its reviewed content/dependency hashes match the
    # live plan; a fresh verdict for the same item replaces the row.
    salvaged_task_reviews: tuple[dict[str, object], ...] = ()
    # Full worker evidence payloads salvaged from a failed Analyst evidence
    # wave (one worker died after the others delivered usable packets).  A
    # retry re-dispatches only the missing workers; these payloads stand in
    # for the finished ones so the retry's fan-in still sees their evidence.
    # Each row is stamped with the revision it was salvaged under and only
    # replays while that revision is still current.
    salvaged_worker_payloads: tuple[dict[str, object], ...] = ()

    def next_menxia_item(self) -> ReviewTaskItem | None:
        completed = set(self.completed_item_ids)
        items = sorted(self.task_items, key=lambda item: (item.order, item.item_id))
        for item in items:
            if item.item_id in completed:
                continue
            if set(item.dependencies).issubset(completed):
                return item
        return None

    def seed_menxia_items(self) -> tuple[MenxiaItemState, ...]:
        """Return menxia pipeline rows, seeding missing ready items.

        A row is created (stage ``SOLVING``) for every task item whose
        dependencies are satisfied and which has no row yet.  Legacy
        snapshots with an empty ``menxia_items`` therefore pick up the first
        ready item exactly like ``next_menxia_item`` used to, while the
        concurrent mode picks up every ready item.
        """

        known = {state.item_id for state in self.menxia_items}
        completed = set(self.completed_item_ids)
        seeded = list(self.menxia_items)
        for item in sorted(self.task_items, key=lambda entry: (entry.order, entry.item_id)):
            if item.item_id in known or item.item_id in completed:
                continue
            if not set(item.dependencies).issubset(completed):
                continue
            seeded.append(MenxiaItemState(item_id=item.item_id, group_id=item.group_id))
        return tuple(seeded)

    def seed_menxia_groups(self) -> tuple[MenxiaGroupState, ...]:
        """Return menxia group rows, seeding missing ready groups.

        A row is created (stage ``SOLVING``) for every task group that has at
        least one dispatchable item (dependencies satisfied, not completed)
        and no row yet.  Groups keep one shared document, so the whole group
        advances as a single pipeline record.

        When the review tracks Zhongshu convergence rows, only groups the
        freeze check already marked FROZEN are seeded: the group pipeline
        takes over finished groups while the rest keep grinding in Zhongshu
        (early hand-over).  Legacy snapshots without Zhongshu rows keep the
        unfiltered behavior.
        """

        known = {state.group_id for state in self.menxia_groups}
        completed = set(self.completed_item_ids)
        frozen = (
            {
                row.group_id
                for row in self.zhongshu_groups
                if row.stage == "FROZEN"
            }
            if self.zhongshu_groups
            else None
        )
        zhongshu_docs = (
            {row.group_id: row.doc_markdown for row in self.zhongshu_groups}
            if self.zhongshu_groups
            else {}
        )
        seeded = list(self.menxia_groups)
        for group in sorted(self.task_groups, key=lambda entry: entry.group_id):
            if group.group_id in known:
                continue
            if frozen is not None and group.group_id not in frozen:
                continue
            members = [
                item for item in self.task_items
                if item.group_id == group.group_id
            ]
            dispatchable = [
                item for item in members
                if item.item_id not in completed
                and set(item.dependencies).issubset(completed)
            ]
            if not dispatchable:
                continue
            seeded.append(MenxiaGroupState(
                group_id=group.group_id,
                # The frozen group's requirement document rides along so the
                # critic's approval map can be closed against it (§9).
                requirement_markdown=zhongshu_docs.get(group.group_id, ""),
            ))
        return tuple(seeded)

    def seed_zhongshu_groups(self) -> tuple[ZhongshuGroupState, ...]:
        """Return zhongshu group rows, seeding every missing task group.

        Unlike the Menxia seeding, no dispatchability filter applies: the
        Zhongshu convergence tracks every task group from the start, because
        a group whose items are all still dependency-blocked still needs a
        row once its turn comes and the stall budget must be per group.
        """

        known = {state.group_id for state in self.zhongshu_groups}
        seeded = list(self.zhongshu_groups)
        for group in sorted(self.task_groups, key=lambda entry: entry.group_id):
            if group.group_id in known:
                continue
            seeded.append(ZhongshuGroupState(group_id=group.group_id))
        return tuple(seeded)


@dataclass(frozen=True)
class AuditState:
    """Operational history kept outside business decisions."""

    sent_notification_keys: tuple[str, ...] = ()
    heartbeat_count: int = 0
    last_heartbeat_at: str | None = None
    reply_history_ref: str | None = None
    last_artifact_id: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class HumanGateState:
    """Pending human decision and the state to resume afterwards."""

    decision_id: str
    reason_code: str
    resume_state: str
    message_id: str | None = None
    # Zhongshu group drainout: one row per parked group with its open finding
    # ids and unapproved items, so the operator (and the resume payload of the
    # directed unstick flow) can see exactly which groups need intervention.
    zhongshu_group_parks: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ReviewTaskItem:
    """Immutable Menxia task-graph item projection."""

    item_id: str
    group_id: str
    dependencies: tuple[str, ...] = ()
    order: int = 0
    status: str = "PENDING"
    title: str = ""
    objective: str = ""
    source_requirement_ids: tuple[str, ...] = ()
    acceptance_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewTaskGroup:
    """Immutable Menxia group projection."""

    group_id: str
    item_ids: tuple[str, ...] = ()
    order: int = 0
    status: str = "PENDING"


@dataclass(frozen=True)
class ReviewTaskRecord:
    """Last completed per-task Critic verdict, keyed by content hashes.

    The record lets the orchestrator re-review only the tasks whose content or
    dependency endpoints changed since the previous revision, instead of the
    whole graph on every convergence round.  ``changes_rounds`` counts the
    consecutive rounds the task kept coming back as ``CHANGES_REQUIRED``, which
    is the per-task stall signal.
    """

    item_id: str
    task_hash: str = ""
    dependency_hash: str = ""
    status: str = "PENDING"
    changes_rounds: int = 0


@dataclass(frozen=True)
class ItemWorkflow:
    """Per-item pipeline row for the Zhongshu revision workflow (A2/P2).

    The authoritative verdict record stays in ``task_review_ledger``; this row
    is the *dispatch table* the ItemWorkflow executor (A2/P3) consumes: which
    items have open stoves, which phase each is in, how many item-local
    revision rounds were consumed, and which findings the last review attached.

    Phases: ``REVIEWING`` (verdict folded, first sight), ``REVISING`` (Solver
    owes a patch), ``APPROVED`` (ratchet-consistent approval), ``ESCALATED``
    (item-local stall budget spent, headed to a human gate).
    """

    item_id: str
    group_id: str = ""
    phase: str = "REVIEWING"
    rounds: int = 0
    last_verdict: str = ""
    finding_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ItemWorkflow":
        if not isinstance(value, Mapping):
            raise ValueError("item workflow must be an object")
        item_id = str(value.get("item_id") or "").strip()
        if not item_id:
            raise ValueError("item workflow requires item_id")
        try:
            rounds = max(0, int(value.get("rounds") or 0))
        except (TypeError, ValueError):
            rounds = 0
        finding_ids = value.get("finding_ids", ())
        if isinstance(finding_ids, str) or finding_ids is None:
            finding_ids = () if finding_ids is None else (str(finding_ids),)
        elif isinstance(finding_ids, (list, tuple)):
            finding_ids = tuple(str(item) for item in finding_ids)
        else:
            raise ValueError("item workflow finding_ids must be an array")
        return cls(
            item_id=item_id,
            group_id=str(value.get("group_id") or "").strip(),
            phase=str(value.get("phase") or "REVIEWING"),
            rounds=rounds,
            last_verdict=str(value.get("last_verdict") or ""),
            finding_ids=tuple(finding_ids),
        )


@dataclass(frozen=True)
class MenxiaItemState:
    """Per-item Menxia pipeline record (stage barrier model).

    ``stage`` is the item-local sub-phase inside the linear ``MENXIA_*``
    states: ``SOLVING`` / ``ANALYZING`` / ``REVIEWING`` are the active
    stages a wave can dispatch; ``APPROVED`` / ``REMOVED`` are folded
    terminal stages; ``ESCALATED`` / ``BLOCKED`` are parked for the group
    gate.  ``revision_round`` is attempt-scoped to this item and feeds the
    stall budget; ``fingerprint`` is the last folded reply's fingerprint for
    no-progress detection.
    """

    item_id: str
    group_id: str = ""
    stage: str = "SOLVING"
    revision_round: int = 0
    last_verdict: str = ""
    fingerprint: str = ""
    blocked_reason: str = ""
    # Bounded copy of the item Solver's latest ``implementation_proposal``.
    # The Item Critic must weigh the proposal against fresh evidence, but the
    # fan-in rows used to drop it, so the Critic re-demanded a proposal the
    # run already possessed and the item stalled on phantom gaps.
    last_solver_proposal: dict[str, object] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MenxiaItemState":
        if not isinstance(value, Mapping):
            raise ValueError("menxia item state must be an object")
        item_id = str(value.get("item_id") or "").strip()
        if not item_id:
            raise ValueError("menxia item state requires item_id")
        try:
            revision_round = max(0, int(value.get("revision_round") or 0))
        except (TypeError, ValueError):
            revision_round = 0
        return cls(
            item_id=item_id,
            group_id=str(value.get("group_id") or "").strip(),
            stage=str(value.get("stage") or "SOLVING"),
            revision_round=revision_round,
            last_verdict=str(value.get("last_verdict") or ""),
            fingerprint=str(value.get("fingerprint") or ""),
            blocked_reason=str(value.get("blocked_reason") or ""),
            last_solver_proposal=(
                dict(value["last_solver_proposal"])
                if isinstance(value.get("last_solver_proposal"), Mapping)
                else None
            ),
        )


@dataclass(frozen=True)
class MenxiaGroupState:
    """Per-group Menxia pipeline record (shared document model).

    One row drives the whole group review loop: the group shares one
    versioned plan document, so ``doc_version``/``doc_hash`` form the
    version chain the joiner checks, ``revision_round`` is the group-wide
    stall budget (every member item consumes the same counter), and
    ``last_open_count``/``stalled_rounds`` feed the suggestion-ledger stall
    rule (open count not shrinking for consecutive rounds, or the same
    suggestion id bouncing twice, escalates to a human).
    """

    group_id: str
    stage: str = "SOLVING"
    revision_round: int = 0
    doc_version: int = 0
    doc_hash: str = ""
    doc_ref: str = ""
    # The authoritative document markdown.  It rides the review state so the
    # joiner and the dispatch contexts never need an async artifact read;
    # the joiner bounds its size before folding and artifact snapshots keep
    # the audit trail.
    doc_markdown: str = ""
    # The group's requirement document (copied from the Zhongshu row when
    # the wave is seeded) travels with the state so the critic's approval
    # can be checked against the requirement's acceptance map (§9).
    requirement_markdown: str = ""
    last_verdict: str = ""
    fingerprint: str = ""
    blocked_reason: str = ""
    last_open_count: int = 0
    stalled_rounds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MenxiaGroupState":
        if not isinstance(value, Mapping):
            raise ValueError("menxia group state must be an object")
        group_id = str(value.get("group_id") or "").strip()
        if not group_id:
            raise ValueError("menxia group state requires group_id")

        def _int(name: str, default: int = 0) -> int:
            try:
                return max(0, int(value.get(name) or default))
            except (TypeError, ValueError):
                return default

        return cls(
            group_id=group_id,
            stage=str(value.get("stage") or "SOLVING"),
            revision_round=_int("revision_round"),
            doc_version=_int("doc_version"),
            doc_hash=str(value.get("doc_hash") or ""),
            doc_ref=str(value.get("doc_ref") or ""),
            doc_markdown=str(value.get("doc_markdown") or ""),
            requirement_markdown=str(value.get("requirement_markdown") or ""),
            last_verdict=str(value.get("last_verdict") or ""),
            fingerprint=str(value.get("fingerprint") or ""),
            blocked_reason=str(value.get("blocked_reason") or ""),
            last_open_count=_int("last_open_count"),
            stalled_rounds=_int("stalled_rounds"),
        )


@dataclass(frozen=True)
class ZhongshuGroupState:
    """Per-group Zhongshu pipeline record (shared requirements model).

    One row drives the whole group convergence loop: the group converges on
    one shared versioned requirements document, so ``doc_version``/``doc_hash``
    form the version chain the joiner checks, ``revision_round`` is the
    group-wide budget (every revision demand on the group consumes the same
    counter against ``max_zhongshu_group_rounds``), and
    ``last_open_count``/``stalled_rounds`` feed the stall rule (open blocker
    count not shrinking for consecutive rounds parks the group as STALLED).
    ``doc_source_hash`` pins the hash of the source material the current
    document was compiled from, so a revision wave only re-compiles when the
    inputs actually changed.
    """

    group_id: str
    stage: str = "REVIEWING"
    revision_round: int = 0
    last_open_count: int = 0
    stalled_rounds: int = 0
    last_verdict: str = ""
    blocked_reason: str = ""
    doc_version: int = 0
    doc_hash: str = ""
    doc_markdown: str = ""
    doc_source_hash: str = ""
    # Group surface hash at the last APPROVE_GROUP verdict (doc + member task
    # surfaces).  The group approval ratchet holds while the live surface
    # still hashes to this value (2026-09-26 group pipeline).
    approved_surface_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ZhongshuGroupState":
        if not isinstance(value, Mapping):
            raise ValueError("zhongshu group state must be an object")
        group_id = str(value.get("group_id") or "").strip()
        if not group_id:
            raise ValueError("zhongshu group state requires group_id")

        def _int(name: str, default: int = 0) -> int:
            try:
                return max(0, int(value.get(name) or default))
            except (TypeError, ValueError):
                return default

        return cls(
            group_id=group_id,
            stage=str(value.get("stage") or "REVIEWING"),
            revision_round=_int("revision_round"),
            last_open_count=_int("last_open_count"),
            stalled_rounds=_int("stalled_rounds"),
            last_verdict=str(value.get("last_verdict") or ""),
            blocked_reason=str(value.get("blocked_reason") or ""),
            doc_version=_int("doc_version"),
            doc_hash=str(value.get("doc_hash") or ""),
            doc_markdown=str(value.get("doc_markdown") or ""),
            doc_source_hash=str(value.get("doc_source_hash") or ""),
            approved_surface_hash=str(value.get("approved_surface_hash") or ""),
        )


@dataclass(frozen=True)
class ProgressUpdate:
    """Typed changes to the progress aggregate."""

    state: str | None = None
    resume_state: str | None = None
    entered_at: str | None = None


@dataclass(frozen=True)
class ParallelUpdate:
    """Typed changes to the active bounded node execution."""

    active_node_run_id: str | None = None
    clear_active_node_run: bool = False


@dataclass(frozen=True)
class ReviewUpdate:
    """Typed changes to the review aggregate."""

    revision_id: str | None = None
    active_group_id: str | None = None
    active_item_id: str | None = None
    findings: tuple[Finding, ...] | None = None
    zhongshu_revision_round: int | None = None
    freeze_check_attempt: int | None = None
    last_reply_fingerprint: str | None = None
    plan_ref: str | None = None
    plan_hash: str | None = None
    task_graph_ref: str | None = None
    task_items: tuple[ReviewTaskItem, ...] | None = None
    task_groups: tuple[ReviewTaskGroup, ...] | None = None
    completed_item_ids: tuple[str, ...] | None = None
    task_review_ledger: tuple[ReviewTaskRecord, ...] | None = None
    attempted_item_ids: tuple[str, ...] | None = None
    attempted_finding_ids: tuple[str, ...] | None = None
    evidence_packet: dict[str, object] | None = None
    item_workflows: tuple[ItemWorkflow, ...] | None = None
    menxia_items: tuple[MenxiaItemState, ...] | None = None
    menxia_groups: tuple[MenxiaGroupState, ...] | None = None
    zhongshu_groups: tuple[ZhongshuGroupState, ...] | None = None
    requirements: tuple[dict[str, object], ...] | None = None
    plan: dict[str, object] | None = None
    salvaged_task_reviews: tuple[dict[str, object], ...] | None = None
    salvaged_worker_payloads: tuple[dict[str, object], ...] | None = None


def _regrouped_zhongshu_rows(
    current: ReviewState | None,
    update: ReviewUpdate,
    carried: tuple[ZhongshuGroupState, ...],
) -> tuple[ZhongshuGroupState, ...] | None:
    """Re-seed the convergence rows when a fold re-cuts the task groups.

    A regroup invalidates the books: the old rows describe a vanished cut and
    the requirement documents are re-issued with the new graph.  Rows clear
    and re-seed fresh for the new cut (matching ``seed_zhongshu_groups()``
    order), while document fields riding the same fold keep their chain on
    the fresh rows.  ``None`` means no regroup happened and the ordinary
    merge stands; a fold without groups is projection noise, not a re-cut.
    """

    if update.task_groups is None or current is None or not current.task_groups:
        return None
    new_cut = {
        group.group_id for group in update.task_groups if group.group_id
    }
    if not new_cut:
        return None
    old_cut = {
        group.group_id for group in current.task_groups if group.group_id
    }
    if new_cut == old_cut:
        return None
    fresh = {
        group_id: ZhongshuGroupState(group_id=group_id)
        for group_id in sorted(new_cut)
    }
    for row in carried:
        base = fresh.get(row.group_id)
        if base is None:
            continue
        fresh[row.group_id] = replace(
            base,
            doc_version=row.doc_version,
            doc_hash=row.doc_hash,
            doc_markdown=row.doc_markdown,
            doc_source_hash=row.doc_source_hash,
        )
    return tuple(fresh[group_id] for group_id in sorted(fresh))


def apply_review_update(
    current: ReviewState | None,
    update: ReviewUpdate | None,
) -> ReviewState | None:
    """Return the review aggregate as it will look after ``update`` is applied.

    The reducer uses this to commit a decision.  State handlers reuse it to
    project the *post-transition* review into the dispatch effect, so an
    outgoing agent prompt can observe findings produced by the very event being
    handled (for example the Critic's verdict that the Solver must revise).
    """

    if update is None:
        return current
    revision_id = update.revision_id or (current.revision_id if current else "")
    if not revision_id:
        raise InvariantViolation("review update requires revision_id")
    zhongshu_rows = (
        update.zhongshu_groups
        if update.zhongshu_groups is not None
        else current.zhongshu_groups if current else ()
    )
    regrouped = _regrouped_zhongshu_rows(current, update, zhongshu_rows)
    if regrouped is not None:
        zhongshu_rows = regrouped
    return ReviewState(
        revision_id=revision_id,
        active_group_id=(
            update.active_group_id
            if update.active_group_id is not None
            else current.active_group_id if current else None
        ),
        active_item_id=(
            update.active_item_id
            if update.active_item_id is not None
            else current.active_item_id if current else None
        ),
        findings=(
            update.findings
            if update.findings is not None
            else current.findings if current else ()
        ),
        zhongshu_revision_round=(
            update.zhongshu_revision_round
            if update.zhongshu_revision_round is not None
            else current.zhongshu_revision_round if current else 0
        ),
        max_zhongshu_revision_rounds=(
            current.max_zhongshu_revision_rounds if current else 8
        ),
        freeze_check_attempt=(
            update.freeze_check_attempt
            if update.freeze_check_attempt is not None
            else current.freeze_check_attempt if current else 0
        ),
        max_freeze_check_attempts=(
            current.max_freeze_check_attempts if current else 2
        ),
        item_revision_round=(current.item_revision_round if current else 0),
        max_item_revision_rounds=(current.max_item_revision_rounds if current else 5),
        max_menxia_group_rounds=(
            current.max_menxia_group_rounds if current else 5
        ),
        max_zhongshu_group_rounds=(
            current.max_zhongshu_group_rounds if current else 5
        ),
        last_reply_fingerprint=(
            update.last_reply_fingerprint
            if update.last_reply_fingerprint is not None
            else current.last_reply_fingerprint if current else ""
        ),
        plan_ref=(
            update.plan_ref
            if update.plan_ref is not None
            else current.plan_ref if current else None
        ),
        plan_hash=(
            update.plan_hash
            if update.plan_hash is not None
            else current.plan_hash if current else None
        ),
        task_graph_ref=(
            update.task_graph_ref
            if update.task_graph_ref is not None
            else current.task_graph_ref if current else None
        ),
        task_items=(
            update.task_items
            if update.task_items is not None
            else current.task_items if current else ()
        ),
        task_groups=(
            update.task_groups
            if update.task_groups is not None
            else current.task_groups if current else ()
        ),
        completed_item_ids=(
            update.completed_item_ids
            if update.completed_item_ids is not None
            else current.completed_item_ids if current else ()
        ),
        task_review_ledger=(
            update.task_review_ledger
            if update.task_review_ledger is not None
            else current.task_review_ledger if current else ()
        ),
        attempted_item_ids=(
            update.attempted_item_ids
            if update.attempted_item_ids is not None
            else current.attempted_item_ids if current else ()
        ),
        attempted_finding_ids=(
            update.attempted_finding_ids
            if update.attempted_finding_ids is not None
            else current.attempted_finding_ids if current else None
        ),
        evidence_packet=(
            update.evidence_packet
            if update.evidence_packet is not None
            else current.evidence_packet if current else None
        ),
        item_workflows=(
            update.item_workflows
            if update.item_workflows is not None
            else current.item_workflows if current else ()
        ),
        menxia_items=(
            update.menxia_items
            if update.menxia_items is not None
            else current.menxia_items if current else ()
        ),
        menxia_groups=(
            update.menxia_groups
            if update.menxia_groups is not None
            else current.menxia_groups if current else ()
        ),
        zhongshu_groups=zhongshu_rows,
        requirements=(
            update.requirements
            if update.requirements is not None
            else current.requirements if current else ()
        ),
        plan=(
            dict(update.plan)
            if update.plan is not None
            else current.plan if current else None
        ),
        salvaged_task_reviews=(
            update.salvaged_task_reviews
            if update.salvaged_task_reviews is not None
            else current.salvaged_task_reviews if current else ()
        ),
        salvaged_worker_payloads=(
            update.salvaged_worker_payloads
            if update.salvaged_worker_payloads is not None
            else current.salvaged_worker_payloads if current else ()
        ),
    )


@dataclass(frozen=True)
class DeliveryUpdate:
    """Typed changes to the delivery aggregate."""

    active_request_id: str | None = None
    status: str | None = None
    dispatch_operation_id: str | None = None
    external_message_id: str | None = None
    idempotency_key: str | None = None
    phase: str | None = None
    role: str | None = None
    expected_agent_id: str | None = None
    last_sent_at: str | None = None
    attempt: int | None = None
    last_result_ref: str | None = None


@dataclass(frozen=True)
class HumanGateUpdate:
    """Typed changes to the human-gate aggregate."""

    decision_id: str | None = None
    resume_state: str | None = None
    reason_code: str | None = None
    message_id: str | None = None
    clear_gate: bool = False
    zhongshu_group_parks: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AuditUpdate:
    """Typed operational bookkeeping updates."""

    sent_notification_key: str | None = None
    sent_notification_keys: tuple[str, ...] = ()
    heartbeat_count: int | None = None
    last_heartbeat_at: str | None = None
    reply_history_ref: str | None = None
    last_artifact_id: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class RecoveryUpdate:
    """Typed changes to retry and blocking state."""

    retry_count: int | None = None
    blocked_reason: str | None = None
    external_retry_count: int | None = None
    reply_retry_count: int | None = None
    timeout_retry_count: int | None = None
    last_failure: FailureRecord | None = None
    no_progress_count: int | None = None


@dataclass(frozen=True)
class WorkflowContext:
    """Complete immutable workflow snapshot composed from bounded aggregates."""

    identity: TaskIdentity
    progression: ProgressState
    delivery: DeliveryState = field(default_factory=DeliveryState)
    recovery: RecoveryState = field(default_factory=RecoveryState)
    review: ReviewState | None = None
    human_gate: HumanGateState | None = None
    request: RequestState = field(default_factory=RequestState)
    parallel: ParallelState = field(default_factory=ParallelState)
    audit: AuditState = field(default_factory=AuditState)
    schema_version: str = "4.0"


def context_to_dto(context: WorkflowContext) -> dict[str, object]:
    """Serialize the immutable aggregate without exposing mutable internals."""

    review = None
    if context.review:
        review = asdict(context.review)
        review["findings"] = [asdict(finding) for finding in context.review.findings]
        review["task_items"] = [asdict(item) for item in context.review.task_items]
        review["task_groups"] = [asdict(group) for group in context.review.task_groups]
        review["completed_item_ids"] = list(context.review.completed_item_ids)
        review["attempted_item_ids"] = list(context.review.attempted_item_ids)
        review["attempted_finding_ids"] = (
            list(context.review.attempted_finding_ids)
            if context.review.attempted_finding_ids is not None
            else None
        )
        review["task_review_ledger"] = [
            asdict(record) for record in context.review.task_review_ledger
        ]
        review["item_workflows"] = [
            asdict(workflow) for workflow in context.review.item_workflows
        ]
        review["menxia_items"] = [
            asdict(state) for state in context.review.menxia_items
        ]
        review["menxia_groups"] = [
            asdict(state) for state in context.review.menxia_groups
        ]
        review["zhongshu_groups"] = [
            asdict(state) for state in context.review.zhongshu_groups
        ]
        review["requirements"] = [dict(item) for item in context.review.requirements]
        review["salvaged_task_reviews"] = [
            dict(row) for row in context.review.salvaged_task_reviews
        ]
    return {
        "identity": asdict(context.identity),
        "request": asdict(context.request),
        "progression": asdict(context.progression),
        "delivery": asdict(context.delivery),
        "recovery": {
            **asdict(context.recovery),
            "last_failure": (
                asdict(context.recovery.last_failure)
                if context.recovery.last_failure is not None
                else None
            ),
        },
        "parallel": {
            "zhongshu": asdict(context.parallel.zhongshu),
            "menxia": asdict(context.parallel.menxia),
            "group_index": context.parallel.group_index,
            "item_index": context.parallel.item_index,
            "menxia_snapshot_ref": context.parallel.menxia_snapshot_ref,
            "active_node_run_id": context.parallel.active_node_run_id,
        },
        "review": review,
        "human_gate": asdict(context.human_gate) if context.human_gate else None,
        "audit": asdict(context.audit),
        "schema_version": context.schema_version,
    }


def context_from_dto(value: Mapping[str, object]) -> WorkflowContext:
    """Deserialize a DTO into fresh immutable value objects."""

    identity = _context_part(value, "identity", TaskIdentity)
    request = _context_part(value, "request", RequestState, default=RequestState())
    progression = _context_part(value, "progression", ProgressState)
    delivery = _context_part(value, "delivery", DeliveryState)
    recovery_value = value.get("recovery")
    recovery = _recovery_from_dto(recovery_value)

    parallel_value = value.get("parallel")
    parallel = _parallel_from_dto(parallel_value)

    review_value = value.get("review")
    review = None
    if review_value is not None and not isinstance(review_value, Mapping):
        raise ValueError("context.review must be null or an object")
    if isinstance(review_value, Mapping):
        findings_value = review_value.get("findings", [])
        if not isinstance(findings_value, list):
            raise ValueError("review.findings must be an array")
        findings = tuple(Finding.from_dict(dict(item)) for item in findings_value)
        task_items = _review_task_items_from_dto(review_value.get("task_items", []))
        task_groups = _review_task_groups_from_dto(review_value.get("task_groups", []))
        completed_item_ids = _completed_item_ids_from_dto(
            review_value.get("completed_item_ids", [])
        )
        task_review_ledger = _task_review_ledger_from_dto(
            review_value.get("task_review_ledger", [])
        )
        raw_item_workflows = review_value.get("item_workflows", [])
        if not isinstance(raw_item_workflows, list):
            raise ValueError("review.item_workflows must be an array")
        item_workflows = tuple(
            ItemWorkflow.from_dict(dict(item))
            for item in raw_item_workflows
            if isinstance(item, Mapping)
        )
        raw_menxia_items = review_value.get("menxia_items", [])
        if not isinstance(raw_menxia_items, list):
            raise ValueError("review.menxia_items must be an array")
        menxia_items = tuple(
            MenxiaItemState.from_dict(dict(item))
            for item in raw_menxia_items
            if isinstance(item, Mapping)
        )
        raw_menxia_groups = review_value.get("menxia_groups", [])
        if not isinstance(raw_menxia_groups, list):
            raise ValueError("review.menxia_groups must be an array")
        menxia_groups = tuple(
            MenxiaGroupState.from_dict(dict(item))
            for item in raw_menxia_groups
            if isinstance(item, Mapping)
        )
        raw_zhongshu_groups = review_value.get("zhongshu_groups", [])
        if not isinstance(raw_zhongshu_groups, list):
            raise ValueError("review.zhongshu_groups must be an array")
        zhongshu_groups = tuple(
            ZhongshuGroupState.from_dict(dict(item))
            for item in raw_zhongshu_groups
            if isinstance(item, Mapping)
        )
        requirements_value = review_value.get("requirements", [])
        if not isinstance(requirements_value, list):
            raise ValueError("review.requirements must be an array")
        requirements = tuple(
            dict(item) for item in requirements_value if isinstance(item, Mapping)
        )
        raw_attempted_findings = review_value.get("attempted_finding_ids")
        raw_evidence_packet = review_value.get("evidence_packet")
        review = ReviewState(
            revision_id=str(review_value.get("revision_id") or ""),
            active_group_id=review_value.get("active_group_id"),
            active_item_id=review_value.get("active_item_id"),
            findings=findings,
            zhongshu_revision_round=int(review_value.get("zhongshu_revision_round", 0)),
            max_zhongshu_revision_rounds=int(review_value.get("max_zhongshu_revision_rounds", 8)),
            freeze_check_attempt=int(review_value.get("freeze_check_attempt", 0)),
            max_freeze_check_attempts=int(review_value.get("max_freeze_check_attempts", 2)),
            item_revision_round=int(review_value.get("item_revision_round", 0)),
            max_item_revision_rounds=int(review_value.get("max_item_revision_rounds", 5)),
            max_menxia_group_rounds=int(
                review_value.get("max_menxia_group_rounds", 5)
            ),
            max_zhongshu_group_rounds=int(
                review_value.get("max_zhongshu_group_rounds", 5)
            ),
            last_reply_fingerprint=str(review_value.get("last_reply_fingerprint") or ""),
            plan_ref=review_value.get("plan_ref"),
            plan_hash=review_value.get("plan_hash"),
            task_graph_ref=review_value.get("task_graph_ref"),
            task_items=task_items,
            task_groups=task_groups,
            completed_item_ids=completed_item_ids,
            task_review_ledger=task_review_ledger,
            item_workflows=item_workflows,
            menxia_items=menxia_items,
            menxia_groups=menxia_groups,
            zhongshu_groups=zhongshu_groups,
            attempted_item_ids=_completed_item_ids_from_dto(
                review_value.get("attempted_item_ids", [])
            ),
            attempted_finding_ids=(
                tuple(str(value) for value in raw_attempted_findings)
                if isinstance(raw_attempted_findings, list)
                else None
            ),
            evidence_packet=(
                dict(raw_evidence_packet)
                if isinstance(raw_evidence_packet, Mapping)
                else None
            ),
            requirements=requirements,
            plan=_plan_from_dto(review_value.get("plan")),
            salvaged_task_reviews=tuple(
                dict(row)
                for row in review_value.get("salvaged_task_reviews", [])
                if isinstance(row, Mapping)
            ),
            salvaged_worker_payloads=tuple(
                dict(row)
                for row in review_value.get("salvaged_worker_payloads", [])
                if isinstance(row, Mapping)
            ),
        )

    gate_value = value.get("human_gate")
    if gate_value is not None and not isinstance(gate_value, Mapping):
        raise ValueError("context.human_gate must be null or an object")
    human_gate = (
        _context_part(value, "human_gate", HumanGateState)
        if isinstance(gate_value, Mapping)
        else None
    )
    audit = _audit_from_dto(value.get("audit"))
    return WorkflowContext(
        identity,
        progression,
        delivery,
        recovery,
        review,
        human_gate,
        request,
        parallel,
        audit,
        str(value.get("schema_version") or "4.0"),
    )


def _context_part(
    value: Mapping[str, object],
    name: str,
    cls: type,
    *,
    default: object | None = None,
):
    item = value.get(name)
    if item is None and default is not None:
        return default
    if not isinstance(item, Mapping):
        raise ValueError(f"context.{name} must be an object")
    return cls(**dict(item))


def _recovery_from_dto(value: object) -> RecoveryState:
    if value is None:
        return RecoveryState()
    if not isinstance(value, Mapping):
        raise ValueError("context.recovery must be an object")
    data = dict(value)
    failure_value = data.get("last_failure")
    if failure_value is not None:
        if not isinstance(failure_value, Mapping):
            raise ValueError("context.recovery.last_failure must be an object or null")
        data["last_failure"] = FailureRecord(**dict(failure_value))
    return RecoveryState(**data)


def _parallel_from_dto(value: object) -> ParallelState:
    if value is None:
        return ParallelState()
    if not isinstance(value, Mapping):
        raise ValueError("context.parallel must be an object")
    zhongshu_value = value.get("zhongshu", {})
    menxia_value = value.get("menxia", {})
    if not isinstance(zhongshu_value, Mapping) or not isinstance(menxia_value, Mapping):
        raise ValueError("context.parallel limits must be objects")
    return ParallelState(
        zhongshu=ZhongshuParallelLimits(**dict(zhongshu_value)),
        menxia=MenxiaParallelLimits(**dict(menxia_value)),
        group_index=value.get("group_index"),
        item_index=value.get("item_index"),
        menxia_snapshot_ref=value.get("menxia_snapshot_ref"),
        active_node_run_id=value.get("active_node_run_id"),
    )


def _audit_from_dto(value: object) -> AuditState:
    if value is None:
        return AuditState()
    if not isinstance(value, Mapping):
        raise ValueError("context.audit must be an object")
    data = dict(value)
    notifications = data.get("sent_notification_keys", ())
    if isinstance(notifications, str) or not isinstance(notifications, (list, tuple)):
        raise ValueError("context.audit.sent_notification_keys must be an array")
    data["sent_notification_keys"] = tuple(str(item) for item in notifications)
    return AuditState(**data)


def _review_task_items_from_dto(value: object) -> tuple[ReviewTaskItem, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("review.task_items must be an array")
    result: list[ReviewTaskItem] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"review.task_items[{index}] must be an object")
        item_id = str(item.get("item_id") or "").strip()
        group_id = str(item.get("group_id") or "").strip()
        if not item_id or not group_id:
            raise ValueError(f"review.task_items[{index}] identity is incomplete")
        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, (list, tuple)):
            raise ValueError(f"review.task_items[{index}].dependencies must be an array")
        order = item.get("order", index)
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError(f"review.task_items[{index}].order must be an integer")
        status = item.get("status", "PENDING")
        if not isinstance(status, str):
            raise ValueError(f"review.task_items[{index}].status must be a string")
        result.append(
            ReviewTaskItem(
                item_id=item_id,
                group_id=group_id,
                dependencies=tuple(str(dependency) for dependency in dependencies),
                order=order,
                status=status,
                title=str(item.get("title") or ""),
                objective=str(item.get("objective") or ""),
                source_requirement_ids=_string_tuple(
                    item.get("source_requirement_ids", []),
                    f"review.task_items[{index}].source_requirement_ids",
                ),
                acceptance_signals=_string_tuple(
                    item.get("acceptance_signals", []),
                    f"review.task_items[{index}].acceptance_signals",
                ),
            )
        )
    return tuple(result)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be an array")
    return tuple(str(item) for item in value)


def _review_task_groups_from_dto(value: object) -> tuple[ReviewTaskGroup, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("review.task_groups must be an array")
    result: list[ReviewTaskGroup] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"review.task_groups[{index}] must be an object")
        group_id = str(item.get("group_id") or "").strip()
        if not group_id:
            raise ValueError(f"review.task_groups[{index}] identity is incomplete")
        item_ids = item.get("item_ids", [])
        if not isinstance(item_ids, (list, tuple)):
            raise ValueError(f"review.task_groups[{index}].item_ids must be an array")
        order = item.get("order", index)
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError(f"review.task_groups[{index}].order must be an integer")
        status = item.get("status", "PENDING")
        if not isinstance(status, str):
            raise ValueError(f"review.task_groups[{index}].status must be a string")
        result.append(
            ReviewTaskGroup(
                group_id=group_id,
                item_ids=tuple(str(item_id) for item_id in item_ids),
                order=order,
                status=status,
            )
        )
    return tuple(result)


def _plan_from_dto(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("review.plan must be an object or null")
    return dict(value)


def _completed_item_ids_from_dto(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("review.completed_item_ids must be an array")
    return tuple(str(item_id) for item_id in value)


def _task_review_ledger_from_dto(value: object) -> tuple[ReviewTaskRecord, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("review.task_review_ledger must be an array")
    result: list[ReviewTaskRecord] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"review.task_review_ledger[{index}] must be an object")
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            raise ValueError(f"review.task_review_ledger[{index}] identity is incomplete")
        result.append(
            ReviewTaskRecord(
                item_id=item_id,
                task_hash=str(item.get("task_hash") or ""),
                dependency_hash=str(item.get("dependency_hash") or ""),
                status=str(item.get("status") or "PENDING"),
                changes_rounds=max(0, int(item.get("changes_rounds") or 0)),
            )
        )
    return tuple(result)


__all__ = [
    "DeliveryState",
    "DeliveryUpdate",
    "HumanGateState",
    "HumanGateUpdate",
    "AuditUpdate",
    "AuditState",
    "MenxiaItemState",
    "MenxiaParallelLimits",
    "ParallelState",
    "ParallelUpdate",
    "ProgressState",
    "ProgressUpdate",
    "RecoveryState",
    "RecoveryUpdate",
    "ReviewState",
    "ReviewTaskGroup",
    "ReviewTaskItem",
    "ReviewUpdate",
    "RequestState",
    "TaskIdentity",
    "ZhongshuGroupState",
    "ZhongshuParallelLimits",
    "WorkflowContext",
    "context_from_dto",
    "context_to_dto",
]

