# Windows Explorer Directory Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rough project import directory picker with a Windows Explorer style folder chooser.

**Architecture:** Extend the local directories API with navigation roots and shortcuts, then render a richer Vue picker with a left sidebar, address bar, selected-folder footer, and directory list. Keep the existing project import flow and absolute path semantics.

**Tech Stack:** Go HTTP API, Vue 3 single-file app, TypeScript types, Node test runner, Go tests.

---

### Task 1: API metadata for Explorer navigation

**Files:**
- Modify: `internal/httpapi/server.go`
- Modify: `internal/httpapi/server_test.go`
- Modify: `web/src/types.ts`

- [ ] Add tests requiring `/api/local-directories` to return `roots` and `shortcuts`.
- [ ] Extend the response type to include roots and shortcuts for both root chooser and directory browsing responses.
- [ ] Implement roots from Windows drive letters and shortcuts from the current user's home/Desktop/Documents/Downloads when present.

### Task 2: Explorer style UI

**Files:**
- Modify: `web/src/App.vue`
- Modify: `web/src/styles.css`
- Modify: `web/tests/card-style-parity.test.mjs`

- [ ] Add tests for Explorer UI markers: sidebar, address bar, selected folder footer, and folder rows.
- [ ] Add path input state so the address bar can jump to an entered absolute path.
- [ ] Render roots and shortcuts in a left navigation pane and directories in a right pane.
- [ ] Keep folder selection separate from navigation: clicking selects, double-clicking opens.

### Task 3: Verify and restart

**Files:**
- Build output: `web/dist`

- [ ] Run Go tests and web unit tests.
- [ ] Run the Vue production build.
- [ ] Restart the local service on port 8766 and verify the directory API returns Explorer metadata.
