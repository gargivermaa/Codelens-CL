# Deployment (₹0 budget)

## Decision

**The full system runs on the laptop in Docker Compose. A free Cloudflare Quick
Tunnel makes it publicly reachable during a demo.** GitHub webhooks arrive through a
free smee.io channel, which works even when the tunnel URL changes.

## Why not a free cloud host

CodeLens needs five long-running services: FastAPI, a Celery worker, Redis, Qdrant
and PostgreSQL. A review run also needs several hundred MB of RAM, because it
indexes whole repositories. Free tiers as of November 2026:

| Option | What the free tier gives | Fit |
|---|---|---|
| Render | Free web services sleep after about 15 min of inactivity (30+ s cold start); 512 MB RAM, 0.1 CPU; free background workers are not clearly available | API only, and it sleeps; no worker, Redis or Qdrant |
| Fly.io | No free tier for new accounts since Oct 2024 (card required) | ✗ |
| Koyeb | Card-free hobby plan reportedly removed in 2026; free database only | ✗ |
| Google Cloud Run | Free quota, but needs a billing account (card) | ✗ (₹0 rule: no billing) |
| Vercel / Netlify / Cloudflare Pages | Free static hosting | Would host the dashboard files only, with no live data unless a backend is reachable |
| **Cloudflare Quick Tunnel** | Free, **no account, no card**; random `https://…trycloudflare.com` URL for a local port; no uptime guarantee; URL changes on every start | ✅ public access to the local stack during a demo |
| **smee.io** | Free stable webhook-forwarding channel | ✅ GitHub webhooks to the laptop |

Sources (checked 18 Nov 2026):
[Render: free tiers 2026](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026),
[flaviocopes: free tiers side by side (Sep 2026)](https://flaviocopes.com/hosting-free-tiers/),
[snapdeploy: state of free hosting](https://snapdeploy.dev/state-of-free-hosting),
[agentdeals: free FastAPI stack](https://agentdeals.dev/free-fastapi-stack),
[Cloudflare docs: Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/index.md).
Free tiers change often. Re-check before relying on them.

Removing Redis, Celery or Qdrant to fit a free host would remove the asynchronous,
repository-aware design that the project is about, so this was not done.

## Architecture of the demo deployment

```
 GitHub ──webhook──▶ smee.io channel ──▶ tunnel container ──▶ api:8000/webhook ─┐
                                                                                 │  (laptop, Docker)
 Browser (anyone with the link + password) ──▶ https://…trycloudflare.com ──▶ api:8000 (dashboard)
                                                                                 │
                         postgres · redis · qdrant · worker ◀────────────────────┘
```

## Commands (Windows PowerShell, in the `codelens-ci` folder)

1. Start the stack, including the webhook forwarder (`SMEE_URL` set in `.env`):
   ```powershell
   docker compose --profile tunnel up --build -d
   ```
2. Set a dashboard password **before** exposing it publicly. In `.env`:
   ```
   DASHBOARD_PASSWORD=choose-a-long-password
   ```
   Restart the API:
   ```powershell
   docker compose up -d api
   ```
3. Install cloudflared once:
   ```powershell
   winget install --id Cloudflare.cloudflared
   ```
4. Open the public URL. Keep this window open during the demo:
   ```powershell
   cloudflared tunnel --url http://localhost:8000
   ```
   It prints `https://<random>.trycloudflare.com`. Open it and sign in as user
   `codelens` with your password. Press Ctrl+C to take it offline.

## Security notes

* With `DASHBOARD_PASSWORD` set, every page and API call needs it, except
  `/webhook` (protected by its HMAC signature) and `/health`.
* Never expose the dashboard publicly without a password.
* Secrets stay in `.env` and `secrets/`, which are never committed or baked into images.
* The tunnel URL is temporary. Share it only for the demo.
