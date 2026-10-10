# ATV desktop app

Notes for running ATV as its own window. The short version is in the [main README](../README.md):
`npm install` once, then `npm start`. Run every command below from the repository root.

## Behaviour

- Closing the window quits the app and stops its server.
- Starting it again while it is running focuses the existing window.
- Window size is remembered in `~/.config/ATV/window.json`. Position is too, except on Wayland,
  which does not let apps place their own windows.
- `npm start -- --config <path>` passes a [config file](../CONFIG.md) through to the server. The app always uses a
  free port and ignores `port` in the config.

## Sandbox error on first start

On recent Ubuntu, Electron aborts with "The SUID sandbox helper binary was found, but is not
configured correctly", because the distro restricts unprivileged user namespaces. Fix it as the
message says. It needs root, and needs redoing whenever `npm install` replaces the Electron binary:

```sh
sudo chown root node_modules/electron/dist/chrome-sandbox && sudo chmod 4755 node_modules/electron/dist/chrome-sandbox
```

## Launcher entry

Optional. To get ATV in the application menu with its own icon, so it can be started without a
terminal, install `atv.desktop` with the path of this checkout filled in. Run it again if the
directory moves:

```sh
sed "s|@REPO@|$PWD|g" atv.desktop > ~/.local/share/applications/atv.desktop
```

To remove it, delete `~/.local/share/applications/atv.desktop`.
