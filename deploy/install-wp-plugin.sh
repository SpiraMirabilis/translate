#!/usr/bin/env bash
#
# Install the Fictioneer REST Meta plugin into a WordPress installation.
#
# Usage:
#   bash install-wp-plugin.sh [/path/to/wordpress]
#
# Defaults to /srv/www/wordpress if no path is given.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_FILE="$SCRIPT_DIR/fictioneer-rest-meta.php"
WP_ROOT="${1:-/srv/www/wordpress}"
PLUGIN_DIR="$WP_ROOT/wp-content/plugins/fictioneer-rest-meta"

# ── Checks ──────────────────────────────────────────────────────────

if [ ! -f "$PLUGIN_FILE" ]; then
    echo "Error: Plugin file not found at $PLUGIN_FILE"
    exit 1
fi

if [ ! -d "$WP_ROOT/wp-content" ]; then
    echo "Error: $WP_ROOT does not look like a WordPress installation."
    echo "       (wp-content/ directory not found)"
    exit 1
fi

# ── Install ─────────────────────────────────────────────────────────

echo "Installing fictioneer-rest-meta plugin..."
echo "  Source: $PLUGIN_FILE"
echo "  Target: $PLUGIN_DIR/"

sudo mkdir -p "$PLUGIN_DIR"
sudo cp "$PLUGIN_FILE" "$PLUGIN_DIR/fictioneer-rest-meta.php"
sudo chown -R www-data:www-data "$PLUGIN_DIR"
sudo chmod 644 "$PLUGIN_DIR/fictioneer-rest-meta.php"

echo "  Plugin files installed."

# ── Activate via WP-CLI if available ────────────────────────────────

if command -v wp &>/dev/null; then
    echo "  Activating plugin via WP-CLI..."
    sudo -u www-data wp plugin activate fictioneer-rest-meta --path="$WP_ROOT" 2>/dev/null \
        && echo "  Plugin activated." \
        || echo "  WP-CLI activation failed. Activate manually in WP Admin > Plugins."
else
    echo "  WP-CLI not found. Activate the plugin in WP Admin > Plugins."
fi

echo ""
echo "Done. Next steps:"
echo "  1. Create a WordPress Application Password (Users > Profile)"
echo "  2. Enter the credentials in T9 Settings > WordPress"
echo "  3. See deploy/wordpress-setup.md for full instructions"
