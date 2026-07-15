#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';

function usage() {
  console.error(`Usage:
  node .agents/skills/nexus-taskrun-submit/taskrun.mjs start --workflowType <type> --taskTitle <title> [--sessionId <id>] [--projectId <project>] [--payloadFile <file>] [--contextFile <file>]
  node .agents/skills/nexus-taskrun-submit/taskrun.mjs submit --payloadFile <file> [--contextFile <file>] [--sessionId <id>] [--startedAt <iso>] [--endedAt <iso>] [--endpoint <url>]`);
  process.exit(2);
}

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) usage();
    const value = argv[++index];
    if (value == null || value.startsWith('--')) usage();
    args[key.slice(2)] = value;
  }
  return args;
}

function isoNow() { return new Date().toISOString(); }
function ensureDir(file) { fs.mkdirSync(path.dirname(file), { recursive: true }); }
function readJson(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
function writeJson(file, value) {
  ensureDir(file);
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}

function start(args) {
  const { workflowType, taskTitle } = args;
  const sessionId = args.sessionId || process.env.CODEX_THREAD_ID;
  if (!workflowType || !taskTitle) usage();
  if (!sessionId || sessionId.trim() === '') throw new Error("SessionId is required. Use CODEX_THREAD_ID or pass --sessionId.");
  const projectId = args.projectId || path.basename(process.cwd());
  const startedAt = args.startedAt || isoNow();
  const safeWorkflowType = workflowType.replace(/[^A-Za-z0-9_.-]/g, '-');
  const payloadFile = args.payloadFile || path.join('.nexus', `task-run-${safeWorkflowType}.json`);
  const contextFile = args.contextFile;
  writeJson(payloadFile, {
    projectId, workflowType, taskTitle, submittedStatus: 'partial_success',
    sessionId: sessionId.trim(), startedAt,
    metrics: { filesChangedCount: 0, testRunCount: 0 },
    evidence: { changedFiles: [], skippedChecks: [], remainingRisks: [] },
  });
  if (contextFile) {
    writeJson(contextFile, {
      projectId, workflowType, taskTitle, sessionId: sessionId.trim(), startedAt,
      payloadFile, startMode: 'local-payload-only',
    });
  }
  console.log(JSON.stringify({ projectId, workflowType, taskTitle, sessionId: sessionId.trim(), startedAt, payloadFile, contextFile, startMode: 'local-payload-only' }, null, 2));
}

async function submit(args) {
  if (!args.payloadFile) usage();
  const payload = readJson(args.payloadFile);
  if (args.contextFile && fs.existsSync(args.contextFile)) {
    const context = readJson(args.contextFile);
    args.sessionId ||= context.sessionId;
    args.startedAt ||= context.startedAt;
  }
  if (args.sessionId) payload.sessionId = args.sessionId.trim();
  if (args.startedAt) payload.startedAt = args.startedAt.trim();
  payload.endedAt = (args.endedAt || isoNow()).trim();
  for (const field of ['projectId', 'workflowType', 'taskTitle', 'submittedStatus', 'sessionId', 'startedAt', 'endedAt', 'metrics', 'evidence']) {
    if (payload[field] == null || (typeof payload[field] === 'string' && payload[field].trim() === '')) throw new Error(`Payload field '${field}' must not be empty.`);
  }
  for (const field of ['workflowId', 'workflowRunId', 'workflowTemplateId', 'workflowCopyId']) {
    if (field in payload) throw new Error(`Payload must not contain '${field}'.`);
  }
  writeJson(args.payloadFile, payload);
  const endpoint = args.endpoint || 'http://127.0.0.1:8766/api/task-runs';
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json; charset=utf-8', Accept: 'application/json' },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`submit task run to ${endpoint} failed: HTTP ${response.status} ${text}`);
  console.log(text);
}

const [command, ...rest] = process.argv.slice(2);
const args = parseArgs(rest);
try {
  if (command === 'start') start(args);
  else if (command === 'submit') await submit(args);
  else usage();
} catch (error) {
  console.error(error?.stack || String(error));
  process.exit(1);
}
