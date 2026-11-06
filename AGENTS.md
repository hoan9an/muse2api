# Working notes for coding agents

How this repository is organized, how it is deployed, and pitfalls already
solved. Read this before making changes.

## What this repo is

Personal fork of [`www222fff/muse2api`](https://github.com/www222fff/muse2api)
— an async FastAPI gateway exposing muse.ai as an OpenAI-compatible API.
It is deployed to the owner's private homelab and also carries one downstream
fix (`MUSE2API_CHROMIUM_FLAGS`) that upstream does not have yet.

## Remotes and sync workflow

- `origin` → this fork (`hoan9an/muse2api`). It is public — GitHub forks of
  public repos cannot be private. **Never commit secrets, `.env`, cookies,
  API keys, internal IPs/hostnames, usernames or network topology.**
- `upstream` → `https://github.com/www222fff/muse2api.git` — the original.

Pull upstream changes, then push to the fork:

```bash
git fetch upstream
git merge upstream/main        # or: git rebase upstream/main
git push origin main
```

## Homelab deployment

Deployed with `docker compose up -d --build` from a clone of this repo on a
private homelab host, reachable over SSH from the dev machine. Host address,
SSH user/key, LAN URL and the real `.env` (API/admin keys) live **only on
that host and in local agent memory** — they are deliberately kept out of
this repository. Ask the owner or check local agentmemory for the concrete
values before deploying.

Config in use: `MUSE2API_DRIVER=browser`,
`MUSE2API_CHROMIUM_FLAGS=--no-sandbox`, `MUSE2API_POOL_STRATEGY=affinity`,
`MUSE2API_ACCOUNT_MAX_CONCURRENCY=4`, `MUSE2API_WARM_THREADS=2`,
keepalive every 600s.

Deploy an update from the dev machine (fill in the real host first):

```bash
git push origin main
ssh <homelab> "cd ~/muse2api && \
  git fetch https://github.com/hoan9an/muse2api.git main && \
  git reset --hard FETCH_HEAD && \
  docker compose up -d --build"
```

(`git reset --hard` is safe: `.env` and `data/` are untracked/ignored.)

Health check after deploy: `GET /healthz` and `GET /readyz` on port 18610.
`readyz` reports `accounts.available`; it stays `0` until a muse.ai account is
imported (dashboard → Accounts → cookie-import extension, or
`POST /admin/accounts`).

## Pitfalls already solved — don't relearn them

1. **Chromium sandbox in Docker**: the image runs as uid 1000 (`USER muse`),
   the kernel blocks Chrome's sandbox and Chromium exits before opening the
   DevTools port → `RuntimeError: Chromium did not expose a DevTools endpoint`.
   Fixed by `MUSE2API_CHROMIUM_FLAGS=--no-sandbox` (commit `7a26cda`). If the
   app runs as root, `--no-sandbox` is added automatically and the flag is not
   needed.
2. **`./data` bind mount ownership**: if Docker creates `data/` as root the
   app crashes with `PermissionError: /app/data/media`. `data/` must be owned
   by uid 1000 — on the homelab, delete it and recreate it as the host user
   whose uid is 1000 (matches the container user).
3. **SSH from Windows PowerShell**: inline heredocs, `$()` and embedded quotes
   get mangled by PowerShell parsing. Write the script locally, `scp` it over,
   then `ssh <host> bash /tmp/script.sh`. Also `curl` in PowerShell is
   `Invoke-WebRequest` — use `curl.exe` for real curl flags.
4. **Repo rename instead of delete**: `gh repo delete` needs the
   `delete_repo` scope which the token may lack; `gh repo rename` frees the
   name without extra scope. Forks of public repos are always public.
5. **`.env` rewrite corrupted keys**: writing `.env` through nested SSH/PS
   quoting concatenated lines and produced
   `MUSE2API_API_KEY=MUSE2API_ADMIN_KEY=...`. Always write env files via an
   scp'd script or `cat > .env` inside a single remote heredoc, then `cat .env`
   back to verify.

## Dev conventions

- Python ≥3.10, src layout under `src/muse2api/`, strict layering:
  `api/` (routes/auth) → `services/` → `accounts/` → `core/` → `drivers/`
  (only drivers talk upstream). See `docs/ARCHITECTURE.md`.
- Tests are offline-only via the mock driver: `pytest`. Lint: `ruff check .`
  (line-length 100, rules `E,F,W,I,B,UP,ASYNC`).
- Commit style: conventional commits as used upstream —
  `feat(browser):`, `fix(config):`, `perf(…)`, `chore: …`.
- New runtime options go through `Settings` in `src/muse2api/config.py`
  (env prefix `MUSE2API_`, documented in `.env.example`).
