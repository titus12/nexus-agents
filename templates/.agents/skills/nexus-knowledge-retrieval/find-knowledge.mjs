import path from "node:path";

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) continue;
    const key = value.slice(2);
    const next = argv[index + 1];
    if (next && !next.startsWith("--")) {
      args[key] = next;
      index += 1;
    } else {
      args[key] = "true";
    }
  }
  return args;
}

function normalizeLocalPath(value) {
  if (!value) return "";
  const resolved = path.resolve(value).replaceAll("\\", "/").replace(/\/+$/, "");
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

async function requestJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${url} returned ${response.status}: ${body.trim()}`);
  }
  return response.json();
}

const args = parseArgs(process.argv.slice(2));
const query = String(args.query || "").trim();
if (!query) {
  console.error('Usage: node .agents/skills/nexus-knowledge-retrieval/find-knowledge.mjs --query "<task or question>"');
  process.exit(2);
}

const endpoint = String(args.endpoint || process.env.NEXUS_URL || "http://127.0.0.1:8766").replace(/\/+$/, "");
const cwd = normalizeLocalPath(args.cwd || process.cwd());
const projects = await requestJson(`${endpoint}/api/projects`);
const matches = projects.filter((project) => {
  const localPath = normalizeLocalPath(project.localPath || project.path);
  return localPath && localPath === cwd;
});

if (matches.length === 0) {
  throw new Error(`Current repository is not imported into Nexus: ${cwd}`);
}
if (matches.length > 1) {
  throw new Error(`Multiple Nexus projects match the current repository: ${matches.map((project) => project.id).join(", ")}`);
}

const project = matches[0];
const params = new URLSearchParams({
  q: query,
  mode: "context",
  maxTokens: String(args.maxTokens || "6000"),
});
const result = await requestJson(
  `${endpoint}/api/projects/${encodeURIComponent(project.id)}/knowledge/retrieve?${params.toString()}`,
);

console.log(JSON.stringify({
  projectId: project.id,
  engine: result.engine || "unknown",
  scope: result.scope || "project",
  projectIds: result.projectIds || [project.id],
  normalizedQuery: result.normalizedQuery || query,
  degraded: Boolean(result.degraded),
  fallbackReason: result.fallbackReason || "",
  usedTokens: result.tokenBudget?.usedTokens || 0,
  maxTokens: result.tokenBudget?.maxTokens || 6000,
  sources: result.sources || [],
}, null, 2));
console.log("");
console.log(result.loadedKnowledgeMarkdown || "## Loaded Knowledge\n\nNo knowledge was returned.");

