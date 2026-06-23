# VoIP UI — Web Management Panel

A React + Vite single-page application that provides a web-based management interface for the `voipd` daemon.  Served by a lightweight Python HTTP backend (`server.py`) and proxied through the UniFiOS nginx instance at `/voip`.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Framework | React 18 + TypeScript |
| Build tool | Vite |
| Styling | Tailwind CSS v3 + shadcn/ui components |
| Package manager | npm (or bun — a `bun.lockb` is included) |
| Backend API | Python 3 stdlib (`http.server`) — no dependencies |

---

## Prerequisites

- **Node.js ≥ 18** (LTS recommended)
- **npm ≥ 9** (comes with Node.js)

Verify:
```sh
node --version   # should print v18.x or higher
npm --version
```

---

## Development

### 1. Install dependencies

```sh
cd ui/panel
npm install
```

### 2. Start the dev server

```sh
npm run dev
```

The app is served at `http://localhost:5173`.  API calls (`/voip/api/*`) will fail unless you also run the Python backend or configure a proxy.

#### Optional: proxy API calls to a live router

Create `ui/panel/vite.config.ts` proxy override (or edit the existing one):

```ts
server: {
  proxy: {
    '/voip/api': {
      target: 'https://<your-router-ip>',
      changeOrigin: true,
      secure: false,
    }
  }
}
```

Replace `<your-router-ip>` with your gateway address.  This lets the dev server forward live API requests to a running `voip-ui` instance on the router.

### 3. Lint

```sh
npm run lint
```

---

## Production build

```sh
cd ui/panel
npm run build
```

Output goes to `ui/panel/dist/`.  The `voip install-ui` command from the project root runs this build and deploys the `dist/` folder to `/data/voip/ui/dist/` on the router.

To build and package manually:

```sh
cd ui/panel
npm run build
tar -czf ../ui-dist.tar.gz -C dist .
```

---

## Project structure

```
ui/
├── panel/                  React + Vite SPA
│   ├── src/
│   │   ├── components/
│   │   │   ├── AppSidebar.tsx      Navigation sidebar + version badge
│   │   │   ├── CommandsPanel.tsx   Install / uninstall / update actions
│   │   │   ├── ConfigPanel.tsx     voipd.conf editor (routing mode, credentials…)
│   │   │   ├── HelpPanel.tsx       Inline documentation
│   │   │   ├── LogsPanel.tsx       Live journalctl log viewer
│   │   │   └── StatusPanel.tsx     Service status, SIP registrar, B2BUA state
│   │   ├── hooks/
│   │   │   ├── useNetworkInterfaces.ts   Fetches WAN/LAN/VPN interface list
│   │   │   └── use-toast.ts
│   │   ├── lib/
│   │   │   ├── api.ts              Typed fetch helpers
│   │   │   └── utils.ts
│   │   ├── pages/
│   │   │   └── Index.tsx           Root page / router
│   │   └── main.tsx
│   ├── public/
│   ├── package.json
│   ├── tailwind.config.ts
│   └── vite.config.ts
├── server.py               Python API backend (runs on router)
├── voip-ui.service         systemd unit for the backend
└── ui-dist.tar.gz          Pre-built dist archive (updated on release)
```

---

## API reference

All endpoints are served by `server.py` at `127.0.0.1:8099` and are reachable via the nginx proxy at `/voip/api/`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/voip/api/status` | Service status, routing mode, SIP proxy, B2BUA state |
| `GET` | `/voip/api/config` | Read `voipd.conf` key-value pairs |
| `POST` | `/voip/api/config` | Write `voipd.conf` and restart `voipd` |
| `GET` | `/voip/api/logs` | Last 150 lines of `journalctl -u voipd` |
| `GET` | `/voip/api/interfaces` | WAN / LAN / VPN interfaces detected on the router |
| `GET` | `/voip/api/rt-tables` | Contents of `/etc/iproute2/rt_tables` |
| `POST` | `/voip/api/command` | Run a predefined command (`restart`, `stop`, `start`, `update`, …) |

All API requests must include the `X-Voip-Token` header injected by the nginx reverse proxy.  Requests without a valid token receive `403 Forbidden`.

---

## Routing mode display

The `ConfigPanel` and `StatusPanel` components are both aware of all four routing modes.  The `routingMode` field returned by `/voip/api/status` uses the raw config value (`b2bua_netns`, `b2bua`, `pbr`, `forward`).

| Config value | UI label |
|---|---|
| `b2bua_netns` | B2BUA NETNS (Sandboxed) — *default* |
| `b2bua` | B2BUA (Deprecated) |
| `pbr` | PBR |
| `forward` | Forward |

---

## Versioning

The UI version is displayed in the sidebar footer (`AppSidebar.tsx`).  Bump it when making user-visible changes:

```tsx
// AppSidebar.tsx
<p className="text-[10px] text-sidebar-foreground">v1.4.0 • GPL-2.0</p>
```

Current version: **v1.4.0**

---

## License

GPL-2.0 — see the root `LICENSE` file.
