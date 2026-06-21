import test from "node:test";
import assert from "node:assert/strict";
import { projectDeleteImpactMessage, upsertProject } from "../.tmp-tests/project-state.js";

function project(id, name = id) {
  return {
    id,
    name,
    path: `D:\\workspace\\src\\${id}`,
    status: "ready",
    updatedAt: "2026-06-20 10:00",
    configSummary: { agents: 1, rules: 1, skills: 1, workflows: 1 },
  };
}

test("upsertProject replaces an existing project with the same id", () => {
  const existing = [project("btd-game-server"), project("nexus-agents")];
  const updated = { ...project("btd-game-server"), repoKey: "gitlab-sh.diandian.info/btd/btd-game-server" };

  const result = upsertProject(existing, updated);

  assert.equal(result.length, 2);
  assert.equal(result[0].repoKey, "gitlab-sh.diandian.info/btd/btd-game-server");
  assert.equal(result[1].id, "nexus-agents");
});

test("upsertProject appends a new project id", () => {
  const existing = [project("btd-game-server")];
  const result = upsertProject(existing, project("new-game"));

  assert.deepEqual(
    result.map((item) => item.id),
    ["btd-game-server", "new-game"],
  );
});

test("projectDeleteImpactMessage explains local metadata removal without source deletion", () => {
  const message = projectDeleteImpactMessage({ ...project("btd-game-server"), localPath: "D:\\workspace\\src\\btd-game-server" });

  assert.match(message, /\.nexus/);
  assert.match(message, /不删除项目源码/);
  assert.match(message, /\.claude \/ \.codex/);
  assert.match(message, /D:\\workspace\\src\\btd-game-server/);
});
