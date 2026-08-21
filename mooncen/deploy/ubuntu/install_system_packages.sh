#!/usr/bin/env bash
set -euo pipefail

NODE_VERSION="${NODE_VERSION:-24.18.0}"
NODE_RELEASE_KEYS_COMMIT="890d535527789c9ebccdccdafd708f60dbd56786"
NODE_RELEASE_KEYRING_SHA256="6030d4e0cd53330acf2ab68acd455b7ca98bb5d5975376f0b7c0892308ba2d57"
CHROME_FOR_TESTING_VERSION="150.0.7871.115"
CHROME_FOR_TESTING_SHA256="1be2db033133c5e2dd1a4e8664bf67b19a61bcf6ed28d2b00f433b3f0b4f9585"
CHROMEDRIVER_SHA256="6ac3919edd107ca13d08cccc118dc83821877e504014233f171bbd94cb01a80e"
CLOUDFLARED_VERSION="2026.6.0"
CLEANUP_DIRS=()

cleanup() {
  local directory
  for directory in "${CLEANUP_DIRS[@]}"; do
    if [ -n "$directory" ] && [ -d "$directory" ]; then
      rm -rf -- "$directory"
    fi
  done
}
trap cleanup EXIT

download_https() {
  local url="$1"
  local output="$2"
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
    --proto-redir '=https' --location --retry 3 --output "$output" "$url"
}

install_verified_node() {
  local dpkg_arch node_arch archive base_url temp_dir actual_keyring_hash expected_archive_hash

  if ! [[ "$NODE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid NODE_VERSION: $NODE_VERSION" >&2
    return 20
  fi

  if command -v node >/dev/null 2>&1 && [ "$(node --version)" = "v${NODE_VERSION}" ]; then
    echo "Node.js v${NODE_VERSION} is already installed."
    return 0
  fi

  dpkg_arch="$(dpkg --print-architecture)"
  case "$dpkg_arch" in
    amd64) node_arch="x64" ;;
    arm64) node_arch="arm64" ;;
    armhf) node_arch="armv7l" ;;
    ppc64el) node_arch="ppc64le" ;;
    s390x) node_arch="s390x" ;;
    *)
      echo "Unsupported Node.js architecture: $dpkg_arch" >&2
      return 21
      ;;
  esac

  archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
  base_url="https://nodejs.org/dist/v${NODE_VERSION}"
  temp_dir="$(mktemp -d)"
  CLEANUP_DIRS+=("$temp_dir")

  download_https \
    "https://raw.githubusercontent.com/nodejs/release-keys/${NODE_RELEASE_KEYS_COMMIT}/gpg/pubring.kbx" \
    "$temp_dir/nodejs-release-keyring.kbx"
  actual_keyring_hash="$(sha256sum "$temp_dir/nodejs-release-keyring.kbx" | awk '{print $1}')"
  if [ "$actual_keyring_hash" != "$NODE_RELEASE_KEYRING_SHA256" ]; then
    echo "Node.js release keyring checksum mismatch." >&2
    rm -rf "$temp_dir"
    return 22
  fi

  download_https "$base_url/SHASUMS256.txt.asc" "$temp_dir/SHASUMS256.txt.asc"
  download_https "$base_url/$archive" "$temp_dir/$archive"
  gpgv --keyring "$temp_dir/nodejs-release-keyring.kbx" \
    --output "$temp_dir/SHASUMS256.txt" < "$temp_dir/SHASUMS256.txt.asc"

  expected_archive_hash="$(awk -v file="$archive" '$2 == file {print $1}' "$temp_dir/SHASUMS256.txt")"
  if ! [[ "$expected_archive_hash" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Node.js archive is absent from the signed checksum manifest: $archive" >&2
    rm -rf "$temp_dir"
    return 23
  fi
  printf '%s  %s\n' "$expected_archive_hash" "$temp_dir/$archive" | sha256sum --check --strict -

  sudo mkdir -p /opt/nodejs
  sudo rm -rf "/opt/nodejs/node-v${NODE_VERSION}-linux-${node_arch}"
  sudo tar -xJf "$temp_dir/$archive" -C /opt/nodejs
  for executable in node npm npx corepack; do
    if [ -e "/opt/nodejs/node-v${NODE_VERSION}-linux-${node_arch}/bin/$executable" ]; then
      sudo ln -sfn "/opt/nodejs/node-v${NODE_VERSION}-linux-${node_arch}/bin/$executable" "/usr/local/bin/$executable"
    fi
  done
  rm -rf "$temp_dir"

  node --version
  npm --version
}

install_verified_cloudflared() {
  local dpkg_arch asset expected_hash temp_dir install_dir install_path version_output

  dpkg_arch="$(dpkg --print-architecture)"
  case "$dpkg_arch" in
    amd64)
      asset="cloudflared-linux-amd64"
      expected_hash="08d27c4c5d3ed73ee3e98ef2ddceb4ad09fd4cfc28e243565a189538e8ccd706"
      ;;
    arm64)
      asset="cloudflared-linux-arm64"
      expected_hash="8482ebf1e74a2a4a1a9f1e090e17e3de08423f94100ece6789287cb26fb9480f"
      ;;
    armhf)
      asset="cloudflared-linux-arm"
      expected_hash="7d854dedec8fc043554d468a29abe1217890b670a00fd29898c0fc39ef1e071c"
      ;;
    i386)
      asset="cloudflared-linux-386"
      expected_hash="dd6a63c418f87dfd51596aac00cf9613cd633aa10282faef1f46afdce813f476"
      ;;
    *)
      echo "Unsupported cloudflared architecture: $dpkg_arch" >&2
      return 24
      ;;
  esac

  temp_dir="$(mktemp -d)"
  CLEANUP_DIRS+=("$temp_dir")
  download_https \
    "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/${asset}" \
    "$temp_dir/cloudflared"
  printf '%s  %s\n' "$expected_hash" "$temp_dir/cloudflared" \
    | sha256sum --check --strict -

  install_dir="/usr/local/lib/mooncen/cloudflared"
  install_path="$install_dir/cloudflared-${CLOUDFLARED_VERSION}"
  sudo install -d -o root -g root -m 0755 "$install_dir"
  sudo install -o root -g root -m 0755 "$temp_dir/cloudflared" "$install_path"
  sudo ln -sfn "$install_path" /usr/bin/cloudflared

  version_output="$(/usr/bin/cloudflared --version)"
  if ! grep -Fq "cloudflared version ${CLOUDFLARED_VERSION}" <<<"$version_output"; then
    echo "Verified cloudflared binary reported an unexpected version: $version_output" >&2
    return 25
  fi
  if ! /usr/bin/cloudflared tunnel run --help 2>&1 | grep -F -- '--token-file' >/dev/null; then
    echo "Verified cloudflared binary does not support --token-file." >&2
    return 26
  fi
  rm -rf "$temp_dir"
}

install_verified_chrome_for_testing() {
  local temp_dir base_url install_dir chrome_path driver_path chrome_version driver_version
  local dependency_manifest apparmor_profile_source
  local -a chrome_dependencies

  temp_dir="$(mktemp -d)"
  CLEANUP_DIRS+=("$temp_dir")
  base_url="https://storage.googleapis.com/chrome-for-testing-public/${CHROME_FOR_TESTING_VERSION}/linux64"
  download_https "$base_url/chrome-linux64.zip" "$temp_dir/chrome-linux64.zip"
  download_https "$base_url/chromedriver-linux64.zip" "$temp_dir/chromedriver-linux64.zip"
  printf '%s  %s\n' "$CHROME_FOR_TESTING_SHA256" "$temp_dir/chrome-linux64.zip" \
    | sha256sum --check --strict -
  printf '%s  %s\n' "$CHROMEDRIVER_SHA256" "$temp_dir/chromedriver-linux64.zip" \
    | sha256sum --check --strict -
  unzip -q "$temp_dir/chrome-linux64.zip" -d "$temp_dir"
  unzip -q "$temp_dir/chromedriver-linux64.zip" -d "$temp_dir"

  chrome_path="$temp_dir/chrome-linux64/chrome"
  driver_path="$temp_dir/chromedriver-linux64/chromedriver"
  dependency_manifest="$temp_dir/chrome-linux64/deb.deps"
  if [ ! -x "$chrome_path" ] || [ ! -x "$driver_path" ] || [ ! -s "$dependency_manifest" ]; then
    echo "Verified Chrome for Testing archives are missing required files." >&2
    return 30
  fi

  # Chrome for Testing publishes its supported Debian dependency expressions
  # inside the hash-pinned archive. Using that manifest prevents a Chrome
  # update from silently outrunning a hand-maintained shared-library list.
  mapfile -t chrome_dependencies < <(sed -e 's/[[:space:]]*$//' -e '/^[[:space:]]*$/d' "$dependency_manifest")
  if [ "${#chrome_dependencies[@]}" -eq 0 ]; then
    echo "Chrome for Testing dependency manifest is empty." >&2
    return 30
  fi
  sudo apt-get satisfy -y --no-install-recommends "${chrome_dependencies[@]}"

  install_dir="/opt/chrome-for-testing/${CHROME_FOR_TESTING_VERSION}"
  if [ -L /opt/chrome-for-testing ]; then
    echo "/opt/chrome-for-testing must not be a symbolic link." >&2
    return 30
  fi
  sudo rm -rf -- "$install_dir"
  sudo install -d -o root -g root -m 0755 /opt/chrome-for-testing
  sudo install -d -o root -g root -m 0755 "$install_dir"
  sudo cp -a "$temp_dir/chrome-linux64" "$install_dir/"
  sudo cp -a "$temp_dir/chromedriver-linux64" "$install_dir/"
  sudo chown -R root:root "$install_dir"
  sudo chmod -R go-w "$install_dir"
  sudo ln -sfn "$install_dir/chrome-linux64/chrome" /usr/local/bin/mooncen-chrome
  sudo ln -sfn "$install_dir/chromedriver-linux64/chromedriver" /usr/local/bin/mooncen-chromedriver

  # Ubuntu 24.04 restricts unprivileged user namespaces unless an AppArmor
  # attachment explicitly allows them. CfT lives outside Ubuntu's packaged
  # Chrome paths, so install the narrow profile recommended for developer/test
  # Chrome builds. The matched parent tree above is root-owned and not writable
  # by the crawler account.
  apparmor_profile_source="$temp_dir/mooncen-chrome-for-testing.apparmor"
  cat >"$apparmor_profile_source" <<'APPARMOR'
abi <abi/4.0>,
include <tunables/global>

profile mooncen-chrome-for-testing /opt/chrome-for-testing/*/chrome-linux64/chrome flags=(unconfined) {
  userns,

  include if exists <local/mooncen-chrome-for-testing>
}
APPARMOR
  sudo install -o root -g root -m 0644 \
    "$apparmor_profile_source" /etc/apparmor.d/mooncen-chrome-for-testing
  sudo apparmor_parser --replace /etc/apparmor.d/mooncen-chrome-for-testing

  chrome_version="$(/usr/local/bin/mooncen-chrome --version)"
  driver_version="$(/usr/local/bin/mooncen-chromedriver --version)"
  grep -Fq "$CHROME_FOR_TESTING_VERSION" <<<"$chrome_version" || {
    echo "Chrome for Testing reported an unexpected version: $chrome_version" >&2
    return 31
  }
  grep -Fq "ChromeDriver $CHROME_FOR_TESTING_VERSION" <<<"$driver_version" || {
    echo "ChromeDriver reported an unexpected version: $driver_version" >&2
    return 32
  }
  rm -rf "$temp_dir"
}

configure_distro_chromium() {
  local chrome_path="" driver_path="" pair candidate_chrome candidate_driver
  local wrapper_chrome wrapper_driver chrome_version driver_version chrome_build driver_build
  local resolved_chrome apparmor_dir apparmor_profile_source

  # Ubuntu's arm64 packages are snap transition wrappers.  Those wrappers
  # require a login user's snap home/cgroup and cannot run in the dedicated
  # crawler systemd account.  Prefer root-owned native payloads, including the
  # version-matched payload inside the verified distro snap, before wrappers.
  wrapper_chrome="$(command -v chromium-browser 2>/dev/null || command -v chromium 2>/dev/null || true)"
  wrapper_driver="$(command -v chromedriver 2>/dev/null || true)"
  for pair in \
    "/usr/lib/chromium/chromium|/usr/lib/chromium/chromedriver" \
    "/snap/chromium/current/usr/lib/chromium-browser/chrome|/snap/chromium/current/usr/lib/chromium-browser/chromedriver" \
    "/usr/lib/chromium-browser/chromium-browser|/usr/lib/chromium-browser/chromedriver" \
    "${wrapper_chrome}|${wrapper_driver}"; do
    candidate_chrome="${pair%%|*}"
    candidate_driver="${pair#*|}"
    if [ -n "$candidate_chrome" ] && [ -n "$candidate_driver" ] &&
       [ -x "$candidate_chrome" ] && [ -x "$candidate_driver" ]; then
      chrome_path="$candidate_chrome"
      driver_path="$candidate_driver"
      break
    fi
  done
  if [ -z "$chrome_path" ] || [ -z "$driver_path" ]; then
    echo "A distro-verified Chromium and matching ChromeDriver are required." >&2
    return 33
  fi

  chrome_version="$("$chrome_path" --version | grep -oE '[0-9]+(\.[0-9]+){3}' | head -1 || true)"
  driver_version="$("$driver_path" --version | grep -oE '[0-9]+(\.[0-9]+){3}' | head -1 || true)"
  chrome_build="${chrome_version%.*}"
  driver_build="${driver_version%.*}"
  if [ -z "$chrome_version" ] || [ "$chrome_build" != "$driver_build" ]; then
    echo "Chromium and ChromeDriver versions do not match: $chrome_version / $driver_version" >&2
    return 34
  fi
  sudo ln -sfn "$chrome_path" /usr/local/bin/mooncen-chrome
  sudo ln -sfn "$driver_path" /usr/local/bin/mooncen-chromedriver

  resolved_chrome="$(readlink -f "$chrome_path")"
  case "$resolved_chrome" in
    /snap/chromium/*/usr/lib/chromium-browser/chrome)
      apparmor_dir="$(mktemp -d)"
      CLEANUP_DIRS+=("$apparmor_dir")
      apparmor_profile_source="$apparmor_dir/mooncen-chromium-arm64"
      cat >"$apparmor_profile_source" <<'APPARMOR'
abi <abi/4.0>,
include <tunables/global>

profile mooncen-chromium-arm64 /snap/chromium/*/usr/lib/chromium-browser/chrome flags=(unconfined) {
  userns,

  include if exists <local/mooncen-chromium-arm64>
}
APPARMOR
      sudo install -o root -g root -m 0644 \
        "$apparmor_profile_source" /etc/apparmor.d/mooncen-chromium-arm64
      sudo apparmor_parser --replace /etc/apparmor.d/mooncen-chromium-arm64
      rm -rf "$apparmor_dir"
      ;;
  esac
}

reconcile_installed_browser() {
  local arch install_dir chrome_path driver_path chrome_version driver_version

  arch="$(dpkg --print-architecture)"
  if [ "$arch" != "amd64" ]; then
    configure_distro_chromium
    return
  fi

  install_dir="/opt/chrome-for-testing/${CHROME_FOR_TESTING_VERSION}"
  chrome_path="$install_dir/chrome-linux64/chrome"
  driver_path="$install_dir/chromedriver-linux64/chromedriver"
  if [ ! -x "$chrome_path" ] || [ ! -x "$driver_path" ]; then
    echo "Pinned Chrome for Testing runtime is missing; rerun the system package installer." >&2
    return 35
  fi
  if [ ! -f /etc/apparmor.d/mooncen-chrome-for-testing ] || [ -L /etc/apparmor.d/mooncen-chrome-for-testing ]; then
    echo "Pinned Chrome for Testing AppArmor profile is missing or unsafe." >&2
    return 35
  fi

  chrome_version="$("$chrome_path" --version)"
  driver_version="$("$driver_path" --version)"
  grep -Fq "$CHROME_FOR_TESTING_VERSION" <<<"$chrome_version" || return 31
  grep -Fq "ChromeDriver $CHROME_FOR_TESTING_VERSION" <<<"$driver_version" || return 32
  sudo ln -sfn "$chrome_path" /usr/local/bin/mooncen-chrome
  sudo ln -sfn "$driver_path" /usr/local/bin/mooncen-chromedriver
  sudo apparmor_parser --replace /etc/apparmor.d/mooncen-chrome-for-testing
}

if [ "${MOONCEN_INSTALL_LIBRARY_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  age \
  apparmor \
  curl \
  gnupg \
  iproute2 \
  nginx \
  openssh-client \
  openssl \
  postgresql \
  postgresql-contrib \
  postgis \
  python3 \
  python3-venv \
  python3-pip \
  rsync \
  unzip \
  wget \
  xz-utils \
  fonts-noto-cjk \
  libatk1.0-0t64 \
  libatk-bridge2.0-0t64 \
  libcups2t64 \
  libasound2t64 \
  libgbm1 \
  libcairo2 \
  libpango-1.0-0 \
  libxcomposite1 \
  libxdamage1 \
  libxfixes3 \
  libxrandr2 \
  libatspi2.0-0t64 \
  libnss3 \
  libxkbcommon0 \
  libdrm2 \
  libxshmfence1 \
  xdg-utils

install_verified_node
install_verified_cloudflared

arch="$(dpkg --print-architecture)"
if [ "$arch" = "amd64" ]; then
  install_verified_chrome_for_testing
else
  echo "Chrome for Testing linux64 is amd64-only. Installing distro Chromium for $arch."
  if ! sudo apt-get install -y chromium-browser chromium-chromedriver; then
    sudo apt-get install -y chromium chromium-driver
  fi
  configure_distro_chromium
fi

sudo systemctl enable --now postgresql
sudo systemctl enable --now nginx

echo "System packages installed."
