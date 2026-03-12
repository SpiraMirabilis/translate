# WordPress / Fictioneer Publishing Setup

T9 can publish translated books directly to a WordPress site running the
[Fictioneer](https://github.com/Tetrakern/fictioneer) theme. Stories and
chapters are created via the WP REST API, with a small companion plugin that
handles Fictioneer-specific metadata.

## Prerequisites

- WordPress 5.6+ with the **Fictioneer** theme active
- SSH or file access to the WordPress server
- A WordPress user account with **Editor** or **Administrator** role

## 1. Install the T9 WordPress plugin

The plugin is a single PHP file: `deploy/fictioneer-rest-meta.php`.

### Option A — Automated install (SSH)

If T9 and WordPress are on the same server:

```bash
cd /path/to/t9
bash deploy/install-wp-plugin.sh /srv/www/wordpress
```

The script accepts the WordPress root path as an argument (defaults to
`/srv/www/wordpress`). It copies the plugin, sets ownership to `www-data`,
and activates it via WP-CLI if available.

If WordPress is on a **different server**, copy the file over first:

```bash
scp deploy/fictioneer-rest-meta.php user@wp-server:/tmp/
ssh user@wp-server 'bash -s' < deploy/install-wp-plugin.sh /srv/www/wordpress
```

### Option B — Manual install

1. Copy `deploy/fictioneer-rest-meta.php` to your WordPress plugins directory:
   ```
   wp-content/plugins/fictioneer-rest-meta/fictioneer-rest-meta.php
   ```
2. Set ownership so the web server can read it:
   ```bash
   sudo chown -R www-data:www-data /path/to/wp-content/plugins/fictioneer-rest-meta
   ```
3. Activate the plugin in **WP Admin > Plugins**.

### Option C — Upload via WP Admin

1. Zip the plugin:
   ```bash
   cd deploy
   mkdir -p fictioneer-rest-meta
   cp fictioneer-rest-meta.php fictioneer-rest-meta/
   zip -r fictioneer-rest-meta.zip fictioneer-rest-meta
   ```
2. In WP Admin, go to **Plugins > Add New > Upload Plugin** and upload the zip.
3. Activate.

## 2. Create a WordPress Application Password

WordPress Application Passwords are used for REST API authentication.

1. Log in to WP Admin.
2. Go to **Users > Profile** (your user).
3. Scroll to **Application Passwords**.
4. Enter a name (e.g. `T9`) and click **Add New Application Password**.
5. Copy the generated password — it is only shown once.

> **Note:** Application Passwords require HTTPS in production. For local/dev
> setups you may need to add this to `wp-config.php`:
> ```php
> define('WP_ENVIRONMENT_TYPE', 'local');
> ```

## 3. Configure T9

### Via the web UI

1. Open T9 and go to **Settings**.
2. In the **WordPress / Fictioneer** section, enter:
   - **WordPress Site URL**: e.g. `https://novels.example.com`
   - **Username**: your WordPress login email or username
   - **Application Password**: the password from step 2
3. Click **Save**, then **Test Connection**.

### Via environment variables

Add to your `.env` file:

```
WP_URL=https://novels.example.com
WP_USERNAME=your-wp-username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

Restart T9 after editing `.env`.

## 4. Publish a book

1. Go to **Books** in T9.
2. Click the globe icon on the book you want to publish.
3. Set the story status and rating, then click **Publish All**.
4. T9 will:
   - Create (or update) an `fcn_story` post
   - Upload the cover image as the featured image (if set)
   - Create (or update) `fcn_chapter` posts for each chapter
   - Link chapters to the story and set word counts
   - Set the chapter ordering on the story

Re-publishing is safe and incremental — unchanged chapters are skipped,
modified chapters are updated, and new chapters are created.

## Plugin endpoints

The plugin registers these REST endpoints under `wp-json/t9/v1/`:

| Endpoint | Method | Description |
|---|---|---|
| `chapter/{id}/link-story` | POST | Link a chapter to a story, set word count |
| `story/{id}/set-chapters` | POST | Set ordered chapter list |
| `story/{id}/set-meta` | POST | Set story status, rating, description |
| `story/{id}/recalculate-words` | POST | Recalculate word counts for all chapters |

All endpoints require authentication (Editor+ role).

## Troubleshooting

**"WordPress credentials not configured"**
: Check Settings in T9. Credentials are saved to `.env` and persist across restarts.

**Test connection returns 401**
: Verify the Application Password is correct. Make sure HTTPS is enabled
  (or `WP_ENVIRONMENT_TYPE` is set to `local`).

**Chapters show 0 words**
: The word count is set when chapters are linked to the story. For existing
  chapters, use the recalculate endpoint:
  ```bash
  curl -u "user:app-password" -X POST \
    https://your-site.com/wp-json/t9/v1/story/{story_id}/recalculate-words
  ```

**Story created but no chapters appear**
: Check the T9 service logs: `journalctl --user -u t9.service -f`.
  Common causes: the `set-meta` endpoint returning 400 (plugin version
  mismatch), or a network timeout.

**Duplicate stories**
: If a publish fails after creating the story but before saving state,
  re-publishing creates a new story. Delete the duplicate in WP Admin and
  clear the T9 state:
  ```bash
  sqlite3 database.db "DELETE FROM wp_publish_state WHERE book_id = X;"
  ```
