// Desktop shell: starts server.py on a free port and shows it in a window.
const { app, BrowserWindow, dialog, screen, shell } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
let win = null;
let server = null;
let quitting = false;

// --config <path> or --config=<path>; anything else is left to the server's own lookup.
function configArg(argv) {
  const i = argv.findIndex((a) => a === '--config' || a.startsWith('--config='));
  if (i < 0) return [];
  const value = argv[i] === '--config' ? argv[i + 1] : argv[i].slice('--config='.length);
  return value ? ['--config', path.resolve(value)] : [];
}

function fail(title, detail) {
  if (quitting) return;
  quitting = true;
  dialog.showErrorBox(title, detail || '(no output on stderr)');
  app.quit();
}

function startServer() {
  let out = '';
  let err = '';
  let port = null;
  server = spawn('python3', [path.join(ROOT, 'server.py'), '--port', '0', ...configArg(process.argv)], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const timer = setTimeout(() => fail('ATV server did not start', 'No "listening on" line after 15s.\n\n' + err), 15000);

  server.stderr.on('data', (d) => {
    process.stderr.write(d);
    err = (err + d).slice(-8000);
  });
  server.stdout.on('data', (d) => {
    if (port) return;
    out += d;
    // Only complete lines count: a chunk can end mid-number.
    const m = out.slice(0, out.lastIndexOf('\n') + 1).match(/127\.0\.0\.1:(\d+)/);
    if (!m) return;
    port = m[1];
    clearTimeout(timer);
    createWindow(`http://127.0.0.1:${port}`);
  });
  server.on('error', (e) => {
    clearTimeout(timer);
    fail('ATV could not run python3', String(e));
  });
  server.on('exit', (code, signal) => {
    clearTimeout(timer);
    server = null;
    fail(port ? 'ATV server stopped' : 'ATV server failed to start', `exit ${signal || code}\n\n${err}`.trim());
  });
}

function stopServer() {
  quitting = true;
  if (server) server.kill();
  server = null;
}

const boundsFile = () => path.join(app.getPath('userData'), 'window.json');

function loadBounds() {
  const fallback = { width: 1400, height: 900 };
  try {
    const b = JSON.parse(fs.readFileSync(boundsFile(), 'utf8'));
    const ok = ['x', 'y', 'width', 'height'].every((k) => Number.isInteger(b[k]));
    if (!ok || b.width < 300 || b.height < 200) return fallback;
    // Drop a position that is no longer on any screen (monitor unplugged).
    const visible = screen.getAllDisplays().some(({ workArea: a }) =>
      b.x < a.x + a.width && b.x + b.width > a.x && b.y < a.y + a.height && b.y + b.height > a.y);
    return visible ? { x: b.x, y: b.y, width: b.width, height: b.height } : { width: b.width, height: b.height };
  } catch {
    return fallback; // missing or corrupt
  }
}

function createWindow(origin) {
  win = new BrowserWindow({
    ...loadBounds(),
    title: 'ATV',
    icon: path.join(ROOT, 'assets', 'icon.png'),
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (new URL(url).origin !== origin) e.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url) && new URL(url).origin !== origin) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.on('close', () => {
    try {
      fs.mkdirSync(path.dirname(boundsFile()), { recursive: true });
      fs.writeFileSync(boundsFile(), JSON.stringify(win.getNormalBounds()));
    } catch {}
  });
  win.on('closed', () => { win = null; });
  win.loadURL(origin);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  });
  app.on('window-all-closed', () => app.quit());
  app.on('will-quit', stopServer);
  process.on('exit', stopServer);
  for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(sig, () => app.quit());
  // ponytail: SIGKILL or a hard crash of Electron still orphans the server; fixing that
  // needs prctl(PR_SET_PDEATHSIG) on the Python side.
  app.whenReady().then(startServer);
}
