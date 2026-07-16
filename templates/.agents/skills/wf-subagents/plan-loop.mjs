#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const statuses = new Set(["met", "deviated", "unverified", "not_started"]);

function usage() {
  console.error(
    "Usage:\n" +
      "  node plan-loop.mjs init --file <ledger.json> --workflowType <type> [--maxLoops 3]\n" +
      "  node plan-loop.mjs gate --file <ledger.json> --stage <plan|quality>\n" +
      "  node plan-loop.mjs advance --file <ledger.json>\n",
  );
  process.exit(2);
}

function args(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      throw new Error(`Unexpected argument '${token}'.`);
    }
    const key = token.slice(2);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for '--${key}'.`);
    }
    result[key] = value;
    index += 1;
  }
  return result;
}

function readJSON(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    throw new Error(`Read '${file}' failed: ${error.message}`);
  }
}

function writeJSON(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

function requireString(value, label, errors) {
  if (typeof value !== "string" || value.trim() === "") {
    errors.push(`${label} must be a non-empty string.`);
  }
}

function validateBase(ledger) {
  const errors = [];
  if (ledger?.schemaVersion !== 1) errors.push("schemaVersion must equal 1.");
  requireString(ledger?.workflowType, "workflowType", errors);
  if (!Number.isInteger(ledger?.loop?.iteration) || ledger.loop.iteration < 0) {
    errors.push("loop.iteration must be a non-negative integer.");
  }
  if (!Number.isInteger(ledger?.loop?.maxLoops) || ledger.loop.maxLoops < 1) {
    errors.push("loop.maxLoops must be a positive integer.");
  }
  if (!Array.isArray(ledger?.items)) errors.push("items must be an array.");
  if (!Array.isArray(ledger?.quality?.unexpectedChanges)) {
    errors.push("quality.unexpectedChanges must be an array.");
  }
  for (const field of [
    "requiredChecks",
    "passedChecks",
    "skippedRequiredChecks",
    "blockingFindings",
    "majorFindings",
    "minorFindings",
  ]) {
    if (!Number.isInteger(ledger?.quality?.[field]) || ledger.quality[field] < 0) {
      errors.push(`quality.${field} must be a non-negative integer.`);
    }
  }
  if (typeof ledger?.quality?.manualAcceptancePending !== "boolean") {
    errors.push("quality.manualAcceptancePending must be boolean.");
  }
  if (
    Number.isInteger(ledger?.quality?.requiredChecks) &&
    Number.isInteger(ledger?.quality?.passedChecks) &&
    ledger.quality.passedChecks > ledger.quality.requiredChecks
  ) {
    errors.push("quality.passedChecks cannot exceed quality.requiredChecks.");
  }
  if (
    Number.isInteger(ledger?.quality?.requiredChecks) &&
    Number.isInteger(ledger?.quality?.skippedRequiredChecks) &&
    ledger.quality.skippedRequiredChecks > ledger.quality.requiredChecks
  ) {
    errors.push("quality.skippedRequiredChecks cannot exceed quality.requiredChecks.");
  }
  return errors;
}

function validatePlan(ledger) {
  const errors = validateBase(ledger);
  const seen = new Set();
  if (Array.isArray(ledger.items) && ledger.items.length === 0) {
    errors.push("items must contain at least one approved plan item.");
  }
  for (const item of ledger.items || []) {
    requireString(item?.id, "item.id", errors);
    if (seen.has(item?.id)) errors.push(`Duplicate plan item id '${item.id}'.`);
    seen.add(item?.id);
    if (typeof item?.required !== "boolean") errors.push(`item '${item?.id}' required must be boolean.`);
    requireString(item?.expected, `item '${item?.id}' expected`, errors);
    if (!Array.isArray(item?.allowedFiles)) errors.push(`item '${item?.id}' allowedFiles must be an array.`);
    if (!item?.verification || typeof item.verification !== "object") {
      errors.push(`item '${item?.id}' verification must be an object.`);
    } else {
      requireString(item.verification.type, `item '${item?.id}' verification.type`, errors);
      requireString(item.verification.expected, `item '${item?.id}' verification.expected`, errors);
    }
  }
  return errors;
}

function qualitySummary(ledger) {
  const summary = {
    requiredItems: 0,
    metCount: 0,
    deviatedApprovedCount: 0,
    unverifiedCount: 0,
    notStartedCount: 0,
    blockingFindings: ledger.quality.blockingFindings,
    requiredChecks: ledger.quality.requiredChecks,
    passedChecks: ledger.quality.passedChecks,
    skippedRequiredChecks: ledger.quality.skippedRequiredChecks,
    majorFindings: ledger.quality.majorFindings,
    minorFindings: ledger.quality.minorFindings,
    manualAcceptancePending: ledger.quality.manualAcceptancePending,
    unexpectedChanges: ledger.quality.unexpectedChanges,
  };

  for (const item of ledger.items) {
    if (item.required) summary.requiredItems += 1;
    if (item.required && item.status === "met") summary.metCount += 1;
    if (item.status === "deviated" && item.deviationApproved === true) {
      summary.deviatedApprovedCount += 1;
    }
    if (item.status === "unverified") summary.unverifiedCount += 1;
    if (item.status === "not_started") summary.notStartedCount += 1;
  }
  summary.qualityScore = Math.max(
    0,
    100 -
      50 * summary.blockingFindings -
      20 * summary.majorFindings -
      5 * summary.minorFindings -
      10 * summary.skippedRequiredChecks,
  );
  return summary;
}

function validateQuality(ledger) {
  const errors = validatePlan(ledger);
  for (const item of ledger.items || []) {
    if (!statuses.has(item?.status)) {
      errors.push(`item '${item?.id}' status must be met, deviated, unverified, or not_started.`);
      continue;
    }
    if (item.status === "met" || item.status === "deviated") {
      requireString(item.actual, `item '${item.id}' actual`, errors);
      if (!Array.isArray(item.evidence) || item.evidence.length === 0) {
        errors.push(`item '${item.id}' ${item.status} status requires evidence.`);
      }
    }
    if (item.verification?.type === "command") {
      if (!Number.isInteger(item.verification.expectedExitCode)) {
        errors.push(`item '${item.id}' command verification requires expectedExitCode.`);
      }
      if (item.status === "met" && !Number.isInteger(item.verification.actualExitCode)) {
        errors.push(`item '${item.id}' met command verification requires actualExitCode.`);
      }
    }
    if (item.status === "deviated" && item.deviationApproved !== true) {
      errors.push(`item '${item.id}' deviated status requires deviationApproved=true.`);
    }
  }
  return errors;
}

function decision(ledger, summary) {
  if (summary.manualAcceptancePending) {
    return "awaiting_user_acceptance";
  }
  // success when metCount == requiredItems and no unresolved quality condition remains.
  if (
    summary.metCount === summary.requiredItems &&
    summary.unverifiedCount === 0 &&
    summary.notStartedCount === 0 &&
    summary.blockingFindings === 0 &&
    summary.majorFindings === 0 &&
    summary.skippedRequiredChecks === 0 &&
    summary.passedChecks === summary.requiredChecks &&
    summary.unexpectedChanges.length === 0
  ) {
    return "success";
  }
  if (ledger.loop.iteration >= ledger.loop.maxLoops || ledger.loop.newEvidence !== true) {
    return "blocked";
  }
  return "repair";
}

function gate(file, stage) {
  const ledger = readJSON(file);
  const errors = stage === "plan" ? validatePlan(ledger) : validateQuality(ledger);
  if (errors.length > 0) {
    console.error(JSON.stringify({ stage, decision: "invalid", errors }, null, 2));
    process.exit(1);
  }

  if (stage === "plan") {
    ledger.loop.lastDecision = "implement";
    writeJSON(file, ledger);
    console.log(JSON.stringify({ stage, decision: "implement", itemCount: ledger.items.length }, null, 2));
    return;
  }

  const summary = qualitySummary(ledger);
  const next = decision(ledger, summary);
  ledger.quality = { ...ledger.quality, ...summary };
  ledger.loop.lastDecision = next;
  writeJSON(file, ledger);
  console.log(JSON.stringify({ stage, decision: next, quality: summary }, null, 2));
}

function init(file, workflowType, maxLoops) {
  const parsedMaxLoops = Number.parseInt(maxLoops || "3", 10);
  if (!Number.isInteger(parsedMaxLoops) || parsedMaxLoops < 1) {
    throw new Error("--maxLoops must be a positive integer.");
  }
  writeJSON(file, {
    schemaVersion: 1,
    workflowType,
    loop: {
      iteration: 0,
      maxLoops: parsedMaxLoops,
      newEvidence: false,
      lastDecision: "planning",
    },
    items: [],
    quality: {
      requiredChecks: 0,
      passedChecks: 0,
      skippedRequiredChecks: 0,
      blockingFindings: 0,
      majorFindings: 0,
      minorFindings: 0,
      manualAcceptancePending: false,
      unexpectedChanges: [],
    },
  });
  console.log(JSON.stringify({ decision: "planning", file }, null, 2));
}

function advance(file) {
  const ledger = readJSON(file);
  const errors = validateBase(ledger);
  if (errors.length > 0) {
    console.error(JSON.stringify({ decision: "invalid", errors }, null, 2));
    process.exit(1);
  }
  if (ledger.loop.lastDecision !== "repair") {
    throw new Error("advance requires loop.lastDecision to equal 'repair'.");
  }
  if (ledger.loop.iteration >= ledger.loop.maxLoops) {
    throw new Error("advance is unavailable because the loop budget is exhausted.");
  }
  ledger.loop.iteration += 1;
  ledger.loop.newEvidence = false;
  ledger.loop.lastDecision = "diagnosing";
  writeJSON(file, ledger);
  console.log(JSON.stringify({ decision: "diagnosing", iteration: ledger.loop.iteration }, null, 2));
}

try {
  const [command, ...rest] = process.argv.slice(2);
  const options = args(rest);
  if (command === "init" && options.file && options.workflowType) {
    init(options.file, options.workflowType, options.maxLoops);
  } else if (command === "gate" && options.file && (options.stage === "plan" || options.stage === "quality")) {
    gate(options.file, options.stage);
  } else if (command === "advance" && options.file) {
    advance(options.file);
  } else {
    usage();
  }
} catch (error) {
  console.error(error.message);
  process.exit(1);
}
