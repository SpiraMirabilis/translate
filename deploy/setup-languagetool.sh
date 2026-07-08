#!/usr/bin/env bash
#
# setup-languagetool.sh — download and install the LanguageTool HTTP server
# that T9's write-editor grammar check uses (web/api/grammar.py proxies to it).
#
# What this sets up (matching deploy/languagetool.service):
#
#   ~/languagetool/LanguageTool-6.6/      the unzipped LanguageTool release (~400 MB)
#   ~/languagetool/ngrams/                OPTIONAL English ngram data (~15 GB unpacked)
#   ~/languagetool/server.properties      server config (from deploy/languagetool-server.properties)
#   ~/.config/systemd/user/languagetool.service
#
# The server listens on http://127.0.0.1:8081, loopback-only (no --public flag,
# so it rejects non-localhost requests). T9 finds it via LANGUAGETOOL_URL
# (default http://127.0.0.1:8081) and degrades gracefully — grammar checking
# just reports "server unreachable" — if it isn't running.
#
# Usage:
#   bash deploy/setup-languagetool.sh                # core install, no ngrams
#   bash deploy/setup-languagetool.sh --with-ngrams  # also fetch the 8 GB English
#                                                    # ngram data (better detection of
#                                                    # confusion pairs: their/there,
#                                                    # its/it's, ...). Needs ~25 GB free
#                                                    # during unpack, ~15 GB after.
#
# ---------------------------------------------------------------------------
# PORTABILITY NOTES (non-Debian/Ubuntu deployments)
# ---------------------------------------------------------------------------
# This script only assumes a Debian-ish system in TWO places, both marked
# "ADJUST HERE" below:
#
#   1. Installing prerequisites (Java 17+ runtime, curl, unzip).
#      Debian/Ubuntu:  sudo apt install default-jre-headless curl unzip
#      Fedora/RHEL:    sudo dnf install java-17-openjdk-headless curl unzip
#      Arch:           sudo pacman -S jre17-openjdk-headless curl unzip
#      openSUSE:       sudo zypper install java-17-openjdk-headless curl unzip
#      macOS:          brew install openjdk curl unzip
#      The script does NOT auto-install packages; it checks they exist and
#      tells you what's missing, so there is nothing distro-specific to break.
#
#   2. The service manager. This installs a *systemd user unit* — the same
#      pattern t9.service uses. If your system doesn't run systemd (Alpine,
#      macOS, BSDs, containers), skip the unit-install step at the bottom and
#      run the server with your init system of choice; the only command that
#      matters is the ExecStart line, equivalent to:
#
#        java -Xms256m -Xmx2g \
#          -cp ~/languagetool/LanguageTool-6.6/languagetool-server.jar \
#          org.languagetool.server.HTTPServer \
#          --config ~/languagetool/server.properties --port 8081
#
# systemd --user reminder (also in the README): user services stop when you
# log out unless lingering is enabled for your account. That usually needs
# root:  sudo loginctl enable-linger $USER
# ---------------------------------------------------------------------------
set -euo pipefail

# -------- versions & URLs ---------------------------------------------------
# LanguageTool publishes releases at https://languagetool.org/download/
# T9 is tested against 6.6; newer releases usually work — bump here to try.
LT_VERSION="6.6"
LT_ZIP_URL="https://languagetool.org/download/LanguageTool-${LT_VERSION}.zip"

# English ngram data (optional, --with-ngrams). Other languages exist at
# https://languagetool.org/download/ngram-data/ — T9's grammar check is
# English-focused (GRAMMAR_LANGUAGE, default en-US), so English is what the
# server.properties languageModel setting expects.
NGRAM_ZIP_URL="https://languagetool.org/download/ngram-data/ngrams-en-20150817.zip"

# -------- layout (keep in sync with deploy/languagetool.service) ------------
LT_HOME="${HOME}/languagetool"                     # install root
LT_DIR="${LT_HOME}/LanguageTool-${LT_VERSION}"     # unzipped release
NGRAM_DIR="${LT_HOME}/ngrams"                      # ngram indexes (optional)
PROPERTIES="${LT_HOME}/server.properties"          # server config
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # T9 checkout root

WITH_NGRAMS=0
[[ "${1:-}" == "--with-ngrams" ]] && WITH_NGRAMS=1

# -------- 1. prerequisites (ADJUST HERE for your distro) --------------------
# We only *check* for tools; install them with your package manager (see the
# portability notes above for per-distro package names).
missing=()
command -v curl  >/dev/null || missing+=("curl")
command -v unzip >/dev/null || missing+=("unzip")
command -v java  >/dev/null || missing+=("java (17+; Debian/Ubuntu: default-jre-headless)")
if (( ${#missing[@]} )); then
    echo "Missing prerequisites: ${missing[*]}"
    echo "Debian/Ubuntu: sudo apt install default-jre-headless curl unzip"
    exit 1
fi
# LanguageTool 6.x needs Java 17+.
java_major=$(java -version 2>&1 | sed -nE 's/.*version "([0-9]+).*/\1/p' | head -1)
if [[ -n "${java_major}" && "${java_major}" -lt 17 ]]; then
    echo "Java ${java_major} found, but LanguageTool ${LT_VERSION} needs Java 17+." >&2
    exit 1
fi

mkdir -p "${LT_HOME}"

# -------- 2. download + unpack LanguageTool ---------------------------------
if [[ -d "${LT_DIR}" ]]; then
    echo "LanguageTool ${LT_VERSION} already present at ${LT_DIR} — skipping download."
else
    echo "Downloading LanguageTool ${LT_VERSION} (~400 MB)..."
    tmp_zip="${LT_HOME}/LanguageTool-${LT_VERSION}.zip"
    curl -fL --retry 3 -o "${tmp_zip}" "${LT_ZIP_URL}"
    unzip -q -t "${tmp_zip}"                       # integrity check before unpacking
    unzip -q "${tmp_zip}" -d "${LT_HOME}"          # zip contains LanguageTool-X.Y/
    rm -f "${tmp_zip}"
    echo "Unpacked to ${LT_DIR}"
fi

# -------- 3. optional ngram data ---------------------------------------------
# The ngram indexes power statistical confusion-pair rules (their/there,
# its/it's). LanguageTool works fine without them — you just lose those rules.
# They are Lucene indexes read via mmap, so they cost page cache, not JVM heap.
if (( WITH_NGRAMS )); then
    if [[ -d "${NGRAM_DIR}/en" ]]; then
        echo "ngram data already present at ${NGRAM_DIR}/en — skipping download."
    else
        echo "Downloading English ngram data (~8 GB, be patient)..."
        tmp_zip="${LT_HOME}/ngrams-en.zip"
        curl -fL --retry 3 -C - -o "${tmp_zip}" "${NGRAM_ZIP_URL}"
        unzip -q -t "${tmp_zip}"
        mkdir -p "${NGRAM_DIR}"
        unzip -q "${tmp_zip}" -d "${NGRAM_DIR}"    # zip contains en/
        rm -f "${tmp_zip}"
        echo "Unpacked to ${NGRAM_DIR}/en"
    fi
fi

# -------- 4. server.properties ------------------------------------------------
# Template lives in the repo. It hardcodes an absolute home directory because
# Java properties files can't expand systemd's %h — we substitute the current
# user's home at install time. Existing config is never overwritten (you may
# have tuned maxTextLength etc.), so delete it first if you want a fresh copy.
if [[ -f "${PROPERTIES}" ]]; then
    echo "Keeping existing ${PROPERTIES} (delete it and re-run for a fresh copy)."
else
    sed "s|^languageModel=.*|languageModel=${NGRAM_DIR}|" \
        "${REPO_DIR}/deploy/languagetool-server.properties" > "${PROPERTIES}"
    # Without ngram data the server refuses to start if languageModel points
    # at a missing directory — comment the line out in that case. Re-enable it
    # later by uncommenting after running with --with-ngrams.
    if [[ ! -d "${NGRAM_DIR}/en" ]]; then
        sed -i "s|^languageModel=|#languageModel=|" "${PROPERTIES}"
        echo "No ngram data — languageModel commented out in ${PROPERTIES}."
    fi
    echo "Wrote ${PROPERTIES}"
fi

# -------- 5. systemd user unit (ADJUST HERE for non-systemd systems) ---------
# See the portability notes at the top: on non-systemd systems, skip this and
# wire the java command into your own init/supervisor.
if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "${HOME}/.config/systemd/user"
    # The unit uses %h (home) paths and expects this exact layout. If you
    # changed LT_VERSION above, update WorkingDirectory/ExecStart in the unit.
    cp "${REPO_DIR}/deploy/languagetool.service" "${HOME}/.config/systemd/user/"
    if [[ "${LT_VERSION}" != "6.6" ]]; then
        sed -i "s|LanguageTool-6.6|LanguageTool-${LT_VERSION}|g" \
            "${HOME}/.config/systemd/user/languagetool.service"
    fi
    # The unit hardcodes /usr/bin/java; point it at whatever java we found.
    java_bin="$(command -v java)"
    if [[ "${java_bin}" != "/usr/bin/java" ]]; then
        sed -i "s|/usr/bin/java|${java_bin}|" \
            "${HOME}/.config/systemd/user/languagetool.service"
    fi
    systemctl --user daemon-reload
    systemctl --user enable --now languagetool
    echo "Service installed and started (systemctl --user status languagetool)."
    echo "Remember: without lingering, user services stop when you log out —"
    echo "  sudo loginctl enable-linger ${USER}"
else
    echo "No systemd user session detected — skipping service install."
    echo "Start the server manually (or via your init system) with:"
    echo "  java -Xms256m -Xmx2g -cp ${LT_DIR}/languagetool-server.jar \\"
    echo "    org.languagetool.server.HTTPServer --config ${PROPERTIES} --port 8081"
fi

# -------- 6. smoke test --------------------------------------------------------
# The JVM takes a few seconds to come up; poll briefly before declaring victory.
echo -n "Waiting for LanguageTool to answer on :8081 "
for _ in $(seq 1 15); do
    if curl -fsS "http://127.0.0.1:8081/v2/languages" >/dev/null 2>&1; then
        echo; echo "LanguageTool is up: http://127.0.0.1:8081/v2/check"
        echo "Enable grammar checking in T9: Settings → grammar check (or GRAMMAR_CHECK_ENABLED=1)."
        exit 0
    fi
    echo -n "."; sleep 2
done
echo
echo "Server not answering yet — check logs: journalctl --user -u languagetool -f" >&2
exit 1
