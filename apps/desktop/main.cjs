"use strict";

const path = require("node:path");
const { app, BrowserWindow, dialog, Menu, session } = require("electron");
const { createDesktopServer } = require("./server.cjs");
const { composeArgs, loadConfig, runDocker, startStack, writeEnvironment } = require("./runtime.cjs");

if (!app.requestSingleInstanceLock()) app.quit();

let mainWindow;
let webServer;
let stack;
let stopping = false;

function resources() {
  if (app.isPackaged) {
    return {
      webDir: path.join(process.resourcesPath, "web"),
      stackDir: path.join(process.resourcesPath, "stack"),
      imageArchive: path.join(process.resourcesPath, "offline-images.tar.gz"),
    };
  }
  const root = path.resolve(__dirname, "../..");
  return {
    webDir: path.join(root, "apps/web/dist"),
    stackDir: root,
    imageArchive: path.join(__dirname, "offline-images.tar.gz"),
  };
}

async function setStatus(message) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  await mainWindow.webContents.executeJavaScript(
    `document.getElementById('status').textContent = ${JSON.stringify(message)}`,
  ).catch(() => {});
}

async function start() {
  const folders = resources();
  const userData = process.env.SPECTRAL_DESKTOP_USER_DATA || app.getPath("userData");
  const config = await loadConfig(userData);
  webServer = createDesktopServer({
    webDir: folders.webDir,
    apiPort: config.ports.api,
    storagePort: config.ports.storage,
    userId: config.userId,
  });
  await new Promise((resolve, reject) => {
    webServer.once("error", reject);
    webServer.listen(0, "127.0.0.1", resolve);
  });
  const origin = `http://127.0.0.1:${webServer.address().port}`;
  const envFile = await writeEnvironment(userData, config, origin);
  stack = { stackDir: folders.stackDir, envFile, project: config.project };
  await startStack({
    ...stack,
    imageArchive: folders.imageArchive,
    apiPort: config.ports.api,
    onStatus: (message) => void setStatus(message),
  });
  await mainWindow.loadURL(`${origin}/dashboard`);
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(`${origin}/`)) event.preventDefault();
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
}

app.whenReady().then(async () => {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    show: false,
    backgroundColor: "#f5faf7",
    title: "SpectralIndex Desktop",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow.show());
  await mainWindow.loadFile(path.join(__dirname, "startup.html"));
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { label: "Arquivo", submenu: [{ role: "quit", label: "Sair" }] },
    { label: "Visualizar", submenu: [{ role: "reload", label: "Recarregar" }, { role: "togglefullscreen", label: "Tela cheia" }] },
  ]));
  try {
    await start();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await setStatus(message);
    dialog.showMessageBox(mainWindow, {
      type: "error",
      title: "SpectralIndex Desktop não iniciou",
      message: "Não foi possível iniciar os serviços locais.",
      detail: message,
      buttons: ["Fechar"],
    });
  }
});

app.on("before-quit", (event) => {
  if (stopping || !stack) return;
  event.preventDefault();
  stopping = true;
  runDocker([...composeArgs(stack), "stop", "--timeout", "30"], {
    cwd: stack.stackDir,
    timeoutMs: 60_000,
  }).catch(() => {}).finally(() => app.quit());
});

app.on("window-all-closed", () => app.quit());
app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});
