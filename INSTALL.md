# Installing T9 from a fresh clone

This guide takes you from `git clone` to a running T9 instance on SQLite. It was
checked end to end on a clean machine: Ubuntu 26.04, Python 3.14, Node 22,
npm 9. All 1480 Python tests and 313 frontend tests pass after following it.

For background on features, configuration variables, and deployment, see
[README.md](README.md). This file covers only installation.

## 1. System prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Needs the `venv` module (on Debian/Ubuntu it's a separate package) |
| Node.js + npm | Node 18+ | Used to build and serve the React frontend |
| git | any | |

On Debian/Ubuntu:

```bash
sudo apt install python3 python3-venv git nodejs npm
```

If your distro's `nodejs` is older than 18, install a current one from
[NodeSource](https://github.com/nodesource/distributions) or with `nvm`.

You don't need a database server. SQLite is the default, and the database file
is created automatically the first time the app runs.

## 2. Clone

```bash
git clone https://github.com/SpiraMirabilis/translate
cd translate
```

## 3. Python environment

Use a virtual environment. Recent Debian/Ubuntu releases block `pip install` into
the system Python (the "externally-managed-environment" error), so the
`pip install -r ...` commands in the README's Quick Start will fail there
unless you run them inside a venv.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r web/requirements_web.txt
```

The requirements files cap two versions on purpose. Keep the caps when you
upgrade:

- `fastapi<0.137`: from 0.137 on, `app.routes` no longer lists the paths of
  included routers, and the public-app route test depends on that list.
- `pydantic<2.14`: the pinned `mcp==1.12.4` imports a pydantic internal that
  2.14 removed.

`.venv/` is already in `.gitignore`.

### Optional Python packages

Two packages are imported only inside `try` blocks, so the app runs without
them:

- `pypinyin`: pinyin matching in `control_check.py` and `transliteration_audit.py`
- `ipinfo`: IP geolocation for reader statistics (also needs an API key)

Run `pip install pypinyin ipinfo` if you want them. For MySQL instead of
SQLite, run `pip install mysql-connector-python` and see the Database section
of the README.

The `OpenCC` wheel from pip includes its own data files, so you don't need the
`libopencc` system package that the comments in `requirements.txt` mention.

## 4. Frontend

```bash
cd web/frontend
npm ci            # or `npm install`
npm run build     # produces web/frontend/dist/, served by the backend in production mode
cd ../..
```

`npm audit` will report advisories. They don't block installation.

## 5. Configuration (`.env`)

You can start the app without a `.env` file. Create one in the project root
when you want to add secrets:

```env
# At least one provider key, unless you'll only use the claudecode provider
ANTHROPIC_KEY=sk-ant-...
OPENAI_KEY=sk-...

# REQUIRED if the app is reachable by anyone but you
T9_PASSWORD=choose-a-strong-password
```

```bash
chmod 600 .env
```

You can also enter API keys later on the **Settings** page. Non-secret settings
are saved to `settings.json`, which is created on first run. The README lists
every supported variable.

The `claudecode` provider has no API key. It needs the `claude` CLI installed,
logged in, and on the `PATH` of the user running T9.

## 6. Run it

The two modes below use the same backend code. They differ only in how the
frontend is served.

### Development mode (hot reload)

```bash
source .venv/bin/activate      # start_web.sh calls `python3`, so the venv must be active
./start_web.sh
```

Open **http://localhost:5173**. Vite serves the frontend there and forwards
`/api` and `/ws` to the backend on port 8000. Press Ctrl+C to stop both servers.

### Production mode (single process)

```bash
.venv/bin/python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000**. In this mode the backend serves the built
`web/frontend/dist/` itself.

The backend checks for `dist/` only at startup. If you run `npm run build` while
it's running, restart it, or `/` will return 404.

## 7. Verify

```bash
curl -s http://127.0.0.1:8000/api/health
# {"status":"healthy","checks":{"database":"ok","frontend":"ok"},...}
```

If the health check reports `"frontend":"missing"`, step 4's build hasn't run
yet or didn't finish. In development mode on port 5173 the app still works, but
the health check stays "unhealthy" until you build.

To run the test suites:

```bash
pip install pytest
python -m pytest                     # Python: 1480 tests, ~70s
(cd web/frontend && npm test)        # Frontend: 313 tests
```

The Python tests use temporary databases and don't touch your `database.db` or
`settings.json`.

## 8. Running as a service (optional)

The unit files in `deploy/` (`t9.service`, `t9-public.service`,
`t9-watchdog.service`, and others) were written for the original host. They
assume:

- the checkout is at `~/t9`: change `WorkingDirectory` and any `%h/t9` paths
- Python packages are installed system-wide (`/usr/bin/python3`): with the
  venv from step 3, change `ExecStart` to use `%h/translate/.venv/bin/python`
  (or wherever your checkout is)

`t9.service` won't start unless `T9_PASSWORD` is set in `.env`. For
installation, lingering, and reverse-proxy setup, see the README section on
running as a service.

## Files created at runtime

These are all gitignored:

| Path | What |
|---|---|
| `database.db` | SQLite database (schema created and migrated automatically) |
| `settings.json` | Settings saved from the Settings page |
| `.env` | Your secrets (you create this) |
| `.venv/`, `web/frontend/node_modules/`, `web/frontend/dist/` | Installed dependencies and build output |
