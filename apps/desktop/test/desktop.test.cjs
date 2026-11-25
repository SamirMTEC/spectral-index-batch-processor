"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { after, before, test } = require("node:test");
const { createDesktopServer } = require("../server.cjs");
const { loadConfig, writeEnvironment } = require("../runtime.cjs");

let directory;
let backend;
let server;
let origin;

before(async () => {
  directory = await fs.mkdtemp(path.join(os.tmpdir(), "spectral-desktop-test-"));
  await fs.writeFile(path.join(directory, "index.html"), "<h1>Desktop</h1>");
  backend = http.createServer((request, response) => {
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ path: request.url, user: request.headers["x-dev-user-id"] }));
  });
  await new Promise((resolve) => backend.listen(0, "127.0.0.1", resolve));
  server = createDesktopServer({
    webDir: directory,
    apiPort: backend.address().port,
    storagePort: 9000,
    userId: "11111111-1111-4111-8111-111111111111",
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await new Promise((resolve) => server.close(resolve));
  await new Promise((resolve) => backend.close(resolve));
  await fs.rm(directory, { recursive: true, force: true });
});

test("desktop routes use local static content and a stable local identity", async () => {
  const page = await fetch(`${origin}/dashboard`);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /Desktop/);
  const identity = await fetch(`${origin}/desktop-identity`);
  assert.deepEqual(await identity.json(), { userId: "11111111-1111-4111-8111-111111111111" });
});

test("API requests stay on the local proxy", async () => {
  const response = await fetch(`${origin}/api/v1/dashboard`, {
    headers: { "X-Dev-User-Id": "11111111-1111-4111-8111-111111111111" },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    path: "/api/v1/dashboard",
    user: "11111111-1111-4111-8111-111111111111",
  });
});

test("a foreign origin cannot send a mutating API request", async () => {
  const response = await fetch(`${origin}/api/v1/projects`, {
    method: "POST",
    headers: { Origin: "http://other.example", "Content-Type": "application/json" },
    body: "{}",
  });
  assert.equal(response.status, 403);
});

test("desktop settings persist independently of the site", async () => {
  const userData = path.join(directory, "user-data");
  const first = await loadConfig(userData);
  const second = await loadConfig(userData);
  assert.deepEqual(first, second);
  assert.match(first.project, /^spectral-desktop-[0-9a-f]{8}$/);
  const envFile = await writeEnvironment(userData, first, origin);
  const env = await fs.readFile(envFile, "utf8");
  assert.match(env, /SPECTRAL_DEV_AUTH_ENABLED=true/);
  assert.match(env, new RegExp(`DESKTOP_WEB_ORIGIN=${origin.replaceAll(".", "\\.")}`));
  assert.doesNotMatch(env, /api-spectral\.classlab\.com\.br/);
});

test("desktop repairs a port that became unavailable without changing stored data", async () => {
  const userData = path.join(directory, "port-repair");
  const original = await loadConfig(userData);
  const blocker = net.createServer();
  await new Promise((resolve) => blocker.listen(original.ports.postgres, "127.0.0.1", resolve));
  try {
    const repaired = await loadConfig(userData);
    assert.notEqual(repaired.ports.postgres, original.ports.postgres);
    assert.equal(repaired.userId, original.userId);
    assert.equal(repaired.project, original.project);
    assert.deepEqual(repaired.secrets, original.secrets);
  } finally {
    await new Promise((resolve) => blocker.close(resolve));
  }
});
