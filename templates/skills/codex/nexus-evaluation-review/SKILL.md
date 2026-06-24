---
name: nexus-evaluation-review
description: Pull Nexus evaluation evidence and statistics for manual, objective review in project Codex. Use when reviewing whether agents, models, workflows, rules, or skills should change.
---

# nexus-evaluation-review

## Purpose

Use this skill to perform **manual, data-first review** of Nexus evaluation results inside a project Codex session.

This skill is **not** for auto-suggesting changes blindly.

Its job is to:

1. pull objective evaluation data from Nexus
2. inspect evidence, metrics, and historical signals
3. guide a structured manual review
4. produce review conclusions grounded in evidence

## Core principle

The review must be:

- objective
- evidence-first
- statistics-led
- comparable across runs

Do **not** jump directly to:

- “add an agent”
- “change model”
- “rewrite workflow”

until the evidence and comparative data justify it.

## Data sources to pull

At minimum, pull as much of the following as is available:

### 1. Evaluation summary

```text
GET /api/evaluations/summary
```

Use this to inspect:

- workflow metrics
- top issues
- component stats
- dimension stats

### 2. Project health

```text
GET /api/evaluation/projects
```

Use this to inspect:

- total runs
- average score
- success rate
- failed count
- pending count

### 3. Raw evaluation records

```text
GET /api/evaluations
```

Use this to inspect:

- final status
- overall score
- confidence
- scores
- analysis.attribution
- model policy

### 4. Learning cases

```text
GET /api/learning-cases
GET /api/learning-cases/search?q=<query>&limit=<n>
```

Use this to inspect:

- similar successful cases
- similar failed cases
- reusable successful paths
- repeated failure patterns

### 5. Task statistics

```text
GET /api/statistics/tasks?view=<failed|successful|top_scored|low_scored>&range=<24h|7d|30d|all>
```

Use this to inspect:

- low-scored tasks
- failed tasks
- recent deterioration
- recent improvements

## Review workflow

Follow this sequence every time.

### Step 1: Define the review target

State clearly:

- project
- workflow type
- task range
- evaluation IDs or run IDs
- review question

Examples:

- “Why is `ui-feature-development` scoring low in `btd-client`?”
- “Should `go-bugfix` in `btd-game-server` change agent/model/workflow?”

### Step 2: Read evidence before judging

Inspect:

- summary
- final result
- verification
- changed files
- skipped checks
- remaining risks
- Go or Unity specific evidence blocks

Do not infer structural change from score alone.

### Step 3: Read score composition

Inspect:

- overall score
- confidence
- component scores if available
- attribution
- primary causes

Ask:

- Is the low score driven by weak verification?
- Is it driven by context quality?
- Is it driven by too many / too few rules?
- Is it driven by risky workflow type rather than actual execution quality?

### Step 4: Compare against history

Use:

- workflow metrics from summary
- project health
- statistics tasks
- learning case search

Compare:

- current run vs same workflow historical average
- current run vs same agent historical performance
- current run vs same model historical performance
- current failed pattern vs similar successful pattern

### Step 5: Classify the problem objectively

Only classify into one or more of these buckets:

1. **evidence problem**
   - verification weak
   - missing changedFiles
   - skipped checks not justified

2. **execution problem**
   - actual task result weak
   - failed verification
   - regression remains

3. **workflow design problem**
   - missing checkpoints
   - review/test path too weak
   - workflow steps inconsistent with task risk

4. **agent fit problem**
   - current agent repeatedly underperforms for this workflow type

5. **model fit problem**
   - current model repeatedly shows weak confidence / score for this workflow type

6. **rule / skill fit problem**
   - rules missing, overloaded, or irrelevant
   - required skills absent or not effective

### Step 6: Only then discuss possible adjustments

After evidence and comparisons are complete, discuss options such as:

- no change needed
- improve evidence discipline only
- adjust workflow checkpoints
- adjust agent responsibilities
- upgrade / downgrade model tier
- refine rules
- refine skills

Each suggestion must cite the exact evidence that supports it.

## Required review output format

Always structure the result like this:

### Review target
- project:
- workflow:
- run/evaluation scope:
- question:

### Evidence observed
- evidence facts only

### Data observed
- score / confidence / workflow metrics / dimension stats / historical comparison

### Objective diagnosis
- classify issue into evidence / execution / workflow / agent / model / rule-skill

### Options
- option A
- option B
- option C

### Recommendation
- optional, only after evidence-backed comparison

### Confidence
- low / medium / high

## Guardrails

Do not:

- suggest changes without citing evidence
- confuse missing evidence with execution failure
- use one failed run to justify structural workflow redesign
- use one high score to justify broad rollout
- treat machine-generated scores as the only truth

## Typical manual review questions

Use this skill when the user asks:

- should we add / modify an agent?
- should we update model selection?
- should we adjust a workflow?
- should we change rules or skills?
- why did this workflow score low?
- what similar successful or failed cases exist?

