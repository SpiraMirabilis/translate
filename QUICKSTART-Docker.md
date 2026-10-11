# Quickstart: T9 in Docker

You need Docker with the Compose plugin (`docker compose version` should work).
Nothing else is required on the host: Python, Node, and the frontend build all
happen inside the image.

## 1. Start it

**From the published image** (no clone needed):

```bash
mkdir -p t9/data && cd t9
curl -fsSLO https://raw.githubusercontent.com/SpiraMirabilis/translate/main/compose.yaml
docker compose pull
docker compose up -d
```

**Or build it from a clone:**

```bash
git clone https://github.com/SpiraMirabilis/translate
cd translate
mkdir -p data
docker compose up -d --build
```

Creating `data/` yourself first means its files belong to you (see
[Your data](#your-data)). The image runs on x86-64 and ARM64. When `docker compose ps` shows `(healthy)`,
open **http://localhost:8000**.

## 2. Set a password and API keys

The first start creates `./data/`, which holds all of T9's state. Edit
`data/.env`:

```env
T9_PASSWORD=choose-a-strong-password
ANTHROPIC_KEY=sk-ant-...
```

Then restart:

```bash
docker compose restart
```

You can also enter API keys on the **Settings** page, which saves them to the
same `data/.env` file.

## 3. Everyday commands

```bash
docker compose logs -f                        # follow logs
docker compose down                           # stop (data/ is kept)
docker compose pull && docker compose up -d   # upgrade (published image)
git pull && docker compose up -d --build      # upgrade (from a clone)
docker compose run --rm t9 python translator.py --list-books   # run a CLI tool
```

To stay on a specific release instead of `latest`, put `T9_VERSION=1.2.0` in a
`.env` file next to `compose.yaml` (not `data/.env`).

## Your data

Everything persists in `./data` on the host:

- `database.db`: the SQLite database
- `settings.json`: settings saved from the Settings page
- `.env`: your secrets
- `covers/`, `illustrations/`, `epub_cache/`, `logs/` and the downloaded
  dictionary

To back up, stop the container and copy the `data/` directory.

The container runs as whoever owns `data/` (or a mounted `~/.codex`, below), so
the files stay yours and you can edit them without `sudo`. If Docker created
`data/` itself, it's owned by root and the container falls back to uid 1000.
To choose explicitly, set `PUID` and `PGID` in the `.env` file next to
`compose.yaml` (for example `PUID=1001`).

## Using the Codex provider

The image includes the Codex CLI, so the `codex` provider (`cx:gpt-6-luna` and
so on) translates on a ChatGPT subscription. It needs a Codex login. Pick one:

- **Reuse the host's login.** If you already use Codex on this machine, add
  this line to the `.env` file next to `compose.yaml` (not `data/.env`) and run
  `docker compose up -d`:

  ```env
  CODEX_HOME_HOST=~/.codex
  ```

  The container then shares your `~/.codex`, the same way a second Codex
  terminal would. Token refreshes stay in sync, and the container runs as your
  user. Don't copy `auth.json` instead: two copies refresh separately and can
  log each other out.

- **Log in inside the container.** The login is stored in `data/codex`:

  ```bash
  docker compose exec t9 codex login --device-auth
  ```

Check it with `docker compose exec t9 codex login status`, or with the
**Test** button on the Codex card on the Settings page.

## Exposing it beyond this machine

By default, the port is bound to `127.0.0.1` only. Before opening it up:

1. Set `T9_PASSWORD` (step 2).
2. Put a TLS reverse proxy on the host in front of `127.0.0.1:8000`. There's an
   example in `deploy/nginx-reverse-proxy.conf`.
3. Add `T9_SECURE_COOKIE=true` to `data/.env`.

The container trusts forwarded client IPs from the host. This is what makes
rate limits and comment IP bans see real visitors. If the `172.30.57.0/24`
subnet in `compose.yaml` collides with one of your networks, change the subnet
and the two gateway addresses there.

## Optional: separate public reader

To run the public Library/Reader as its own process (as on the production host),
on port 8001 and sharing the same `data/`:

```bash
docker compose --profile public up -d
```

## Not available in the container

- **The `claudecode` provider**: it needs the `claude` CLI, which the image
  doesn't include. Use `codex` or an API-key provider instead.
- **AZW3 (Kindle) export**: it needs Calibre's `ebook-convert`. EPUB export
  works.

For installing without Docker, see [INSTALL.md](INSTALL.md).
