"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const net = require("node:net");
const path = require("node:path");
const { spawn } = require("node:child_process");

const requiredImages = [
  "spectral-index-desktop:0.6.0",
  "postgres:17-alpine",
  "redis:8-alpine",
  "minio/minio:RELEASE.2025-09-07T16-13-09Z",
];

function runDocker(args, { cwd, timeoutMs = 600_000, onOutput = () => {} } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", args, { cwd, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    const capture = (chunk) => {
      const message = chunk.toString("utf8");
      output = (output + message).slice(-4000);
      onOutput(message);
    };
    child.stdout.on("data", capture);
    child.stderr.on("data", capture);
    const timer = setTimeout(() => child.kill(), timeoutMs);
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(output);
      else reject(new Error(`docker ${args.slice(0, 2).join(" ")} falhou (${code}): ${output}`));
    });
  });
}

async function unusedPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function portAvailable(port) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) return false;
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.listen(port, "127.0.0.1", () => server.close(() => resolve(true)));
  });
}

async function loadConfig(userData) {
  await fs.mkdir(userData, { recursive: true });
  const configFile = path.join(userData, "desktop.json");
  try {
    const config = JSON.parse(await fs.readFile(configFile, "utf8"));
    if (config.version === 1 && config.userId && config.project && config.ports && config.secrets) {
      let updated = false;
      const assigned = new Set();
      for (const key of ["postgres", "redis", "storage", "storageConsole", "api"]) {
        const port = config.ports[key];
        if (!(await portAvailable(port)) || assigned.has(port)) {
          let replacement;
          do { replacement = await unusedPort(); } while (assigned.has(replacement));
          config.ports[key] = replacement;
          updated = true;
        }
        assigned.add(config.ports[key]);
      }
      if (updated) await fs.writeFile(configFile, JSON.stringify(config, null, 2), { mode: 0o600 });
      return config;
    }
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const ports = {};
  for (const key of ["postgres", "redis", "storage", "storageConsole", "api"]) {
    ports[key] = await unusedPort();
  }
  const config = {
    version: 1,
    userId: crypto.randomUUID(),
    project: `spectral-desktop-${crypto.randomBytes(4).toString("hex")}`,
    ports,
    secrets: {
      postgres: crypto.randomBytes(24).toString("base64url"),
      storage: crypto.randomBytes(24).toString("base64url"),
    },
  };
  await fs.writeFile(configFile, JSON.stringify(config, null, 2), { mode: 0o600 });
  return config;
}

async function writeEnvironment(userData, config, webOrigin) {
  const environment = {
    POSTGRES_DB: "spectralindex",
    POSTGRES_USER: "spectralindex",
    POSTGRES_PASSWORD: config.secrets.postgres,
    POSTGRES_PORT: config.ports.postgres,
    REDIS_PORT: config.ports.redis,
    MINIO_ROOT_USER: "spectraldesktop",
    MINIO_ROOT_PASSWORD: config.secrets.storage,
    MINIO_API_PORT: config.ports.storage,
    MINIO_CONSOLE_PORT: config.ports.storageConsole,
    API_PORT: config.ports.api,
    SPECTRAL_ENVIRONMENT: "development",
    SPECTRAL_DEV_AUTH_ENABLED: "true",
    DESKTOP_WEB_ORIGIN: webOrigin,
    DESKTOP_STORAGE_URL: `http://127.0.0.1:${config.ports.storage}`,
  };
  const file = path.join(userData, "stack.env");
  await fs.writeFile(file, Object.entries(environment).map(([key, value]) => `${key}=${value}`).join("\n") + "\n", { mode: 0o600 });
  return file;
}

function composeArgs({ stackDir, envFile, project }) {
  return [
    "compose", "--project-name", project,
    "--env-file", envFile,
    "-f", path.join(stackDir, "docker-compose.yml"),
    "-f", path.join(stackDir, "docker-compose.desktop.yml"),
  ];
}

async function ensureImages(archive, onStatus) {
  try {
    await runDocker(["image", "inspect", ...requiredImages], { timeoutMs: 20_000 });
    return;
  } catch {
    onStatus("Preparando os serviços locais para uso offline. Isso pode levar alguns minutos.");
    await fs.access(archive);
    await runDocker(["image", "load", "-i", archive], { timeoutMs: 1_800_000 });
    await runDocker(["image", "inspect", ...requiredImages], { timeoutMs: 20_000 });
  }
}

async function waitForReady(apiPort, onStatus) {
  const deadline = Date.now() + 240_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${apiPort}/ready`, {
        signal: AbortSignal.timeout(3000),
      });
      if (response.ok) return;
    } catch {}
    onStatus("Aguardando banco, armazenamento e API local...");
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw new Error("A API local não ficou pronta em quatro minutos. Verifique os serviços no Docker Desktop.");
}

async function startStack({ stackDir, envFile, project, imageArchive, apiPort, onStatus }) {
  onStatus("Verificando o Docker Desktop...");
  try {
    await runDocker(["info", "--format", "{{.ServerVersion}}"], { timeoutMs: 20_000 });
  } catch {
    throw new Error("Instale e inicie o Docker Desktop com contêineres Linux antes de abrir o SpectralIndex Desktop.");
  }
  await ensureImages(imageArchive, onStatus);
  onStatus("Iniciando os serviços locais...");
  await runDocker([...composeArgs({ stackDir, envFile, project }), "up", "-d", "--no-build", "--pull", "never"], {
    cwd: stackDir,
    timeoutMs: 300_000,
  });
  await waitForReady(apiPort, onStatus);
}

module.exports = { composeArgs, loadConfig, requiredImages, runDocker, startStack, writeEnvironment };
