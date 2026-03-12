# VoIP Control Panel for UniFi OS

Web UI for the [`voipd`](https://github.com/maverick-bytes/voip) VoIP daemon — configure VLAN settings, monitor service state, view live logs, and run management commands, all from a browser tab on your UniFi OS gateway.

Accessible at `https://<gateway-ip>/voip` after running `./voip install-ui`. Login is gated by your existing UniFi OS session — no separate credentials needed.

## Features

- **Status panel** — live service state, VoIP IP, gateway, routing mode, resolved SIP proxy address. Inline Start / Stop / Restart controls.
- **Configuration panel** — edit `voipd.conf` in-browser: WAN interface, VLAN ID, egress CoS, P-CSCF hostname, routing mode (PBR or Forward), routing table number, IMS subnet override. Saves and restarts `voipd` with a confirmation step.
- **Logs panel** — live `journalctl` output with level colour-coding, auto-scrolls to bottom.
- **Commands panel** — Install, Reinstall, Update, Verify, and scoped Uninstall (daemon only / UI only / everything), each with a post-execution result view showing full terminal output.
- **Help panel** — quick-start guide and FAQ.

## Tech stack

- React 18 + TypeScript
- Vite (builds to a static bundle served by `server.py`)
- shadcn/ui + Tailwind CSS
- Python 3 stdlib HTTP server (`server.py`) — runs as `voip-ui.service` on `127.0.0.1:8099`

## Development

```sh
# Install dependencies
npm install

# Start dev server with proxy to the router API (192.168.5.1)
npm run dev

# Production build (outputs to dist/)
npm run build
```

The Vite dev proxy in `vite.config.ts` forwards `/voip/api/*` to `http://127.0.0.1:8099` so you can develop against the real API server running on the router (via SSH tunnel or direct LAN access).

## Deployment

Build and package for GitHub Releases:

```sh
npm run build
cd dist && tar -czf ../ui-dist.tar.gz . && cd ..
```

Upload `ui-dist.tar.gz` as a release asset. The `./voip install-ui` and `./voip update` commands will download and extract it automatically.

## License

GPL-2.0 — see the repo root for details.
