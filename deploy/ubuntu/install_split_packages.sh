#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 web|db|crawler" >&2
  exit 64
fi

role="$1"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

case "$role" in
  web|db|crawler) ;;
  *)
    echo "Role must be web, db, or crawler." >&2
    exit 64
    ;;
esac

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update

if [ "$role" = "db" ]; then
  sudo apt-get install -y --no-install-recommends \
    ca-certificates \
    openssl \
    postgresql \
    postgresql-contrib \
    postgresql-postgis \
    postgis \
    python3 \
    python3-dotenv \
    python3-psycopg2 \
    rsync
  sudo systemctl enable --now postgresql
  echo "MoonCen split DB packages installed."
  exit 0
fi

if [ "$role" = "crawler" ]; then
  sudo apt-get install -y --no-install-recommends \
    apparmor \
    ca-certificates \
    curl \
    fonts-noto-cjk \
    gnupg \
    iproute2 \
    openssl \
    postgresql \
    postgresql-contrib \
    postgresql-postgis \
    postgis \
    python3 \
    python3-pip \
    python3-venv \
    rsync \
    unzip \
    xz-utils

  # Install only the hash-pinned browser pair used by Selenium crawlers.
  MOONCEN_INSTALL_LIBRARY_ONLY=1
  # shellcheck source=install_system_packages.sh
  source "$script_dir/install_system_packages.sh"
  if [ "$(dpkg --print-architecture)" = "amd64" ]; then
    install_verified_chrome_for_testing
  else
    if ! sudo apt-get install -y chromium-browser chromium-chromedriver; then
      sudo apt-get install -y chromium chromium-driver
    fi
    configure_distro_chromium
  fi

  sudo systemctl enable --now postgresql
  echo "MoonCen split crawler packages installed."
  exit 0
fi

sudo apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  gnupg \
  iproute2 \
  nginx \
  openssl \
  python3 \
  python3-pip \
  python3-venv \
  rsync \
  xz-utils

# Reuse the hash- and signature-verified Node.js installer from the full
# deployment without installing PostgreSQL, Chrome, or cloudflared.
MOONCEN_INSTALL_LIBRARY_ONLY=1
# shellcheck source=install_system_packages.sh
source "$script_dir/install_system_packages.sh"
install_verified_node

sudo systemctl enable --now nginx
echo "MoonCen split web packages installed."
