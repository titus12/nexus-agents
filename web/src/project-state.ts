import type { Project } from "./types";

export function upsertProject(projects: Project[], project: Project): Project[] {
  const index = projects.findIndex((item) => item.id === project.id);
  if (index === -1) {
    return [...projects, project];
  }
  return projects.map((item, currentIndex) => (currentIndex === index ? project : item));
}

export function projectDeleteImpactMessage(project: Project): string {
  const localPath = project.localPath || project.path || "-";
  return [
    `从 Nexus 移除项目：${project.name}`,
    "",
    "确认后会发生：",
    "1. 从 Nexus 项目列表移除该项目。",
    "2. 清除 Nexus 当前服务中的 Project Config Set 扫描结果。",
    "3. 删除项目根目录的 .nexus 本地导入文件。",
    "",
    "不会发生：",
    "1. 不删除项目源码或 Git 仓库。",
    "2. 不删除 .claude / .codex / .agents / .mcp.json。",
    "3. 不删除 workflow 的 .md 或 .graph.json 文件。",
    "",
    `本地路径：${localPath}`,
  ].join("\n");
}
