# Quickstart: T9 in Docker

You need Docker with the Compose plugin (`docker compose version` should work).
Nothing else is required on the host: Python, Node, and the frontend build all
happen inside the image.

## 1. Start it

**From the published image** (no clone needed):

```bash
mkdir t9 && cd t9
curl -fsSLO https://raw.githubusercontent.com/SpiraMirabilis/translate/main/compose.yaml
docker compose pull
docker compose up -d
```

**Or build it from a clone:**

```bash
git clone https://github.com/SpiraMirabilis/translate
cd translate
docker compose up -d --build
```

The image runs on x86-64 and ARM64. When `docker compose ps` shows `(healthy)`,
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

To back up, stop the container and copy the `data/` directory. Files in it are
owned by uid 1000.

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

- **The `claudecode` provider**: it needs the `claude` CLI and a login inside
  the container. Use an API-key provider instead.
- **AZW3 (Kindle) export**: it needs Calibre's `ebook-convert`. EPUB export
  works.

For installing without Docker, see [INSTALL.md](INSTALL.md).
