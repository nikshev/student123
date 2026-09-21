#!/bin/bash
# docker/init/init-secrets.sh — згенерувати секрети у .env (запустити ОДИН раз на хості).
#
#   cp .env.example .env
#   ./docker/init/init-secrets.sh
#
# Заповнює всі *-change-me-* значення, крім AI_TUTOR_LLM_API_KEY
# (ключ провайдера LLM вписується вручну). Потрібні: openssl, ssh-keygen, python3.
set -euo pipefail

cd "$(dirname "$0")/../.."

if [ ! -f .env ]; then
  echo "Немає .env — спочатку: cp .env.example .env" >&2
  exit 1
fi

need() { command -v "$1" >/dev/null 2>&1 || { echo "Потрібен $1" >&2; exit 1; }; }
need openssl
need ssh-keygen
need python3

rand_hex() { openssl rand -hex "${1:-32}"; }

set_kv() { # set_kv KEY VALUE — замінити рядок KEY=... у .env (значення без пробілів/лапок)
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

maybe_set_kv() { # не чіпати значення, якщо вже не плейсхолдер
  local key="$1" value="$2"
  local current
  current="$(grep "^${key}=" .env | cut -d= -f2-)"
  case "$current" in
    ""|"change-me"*) set_kv "$key" "$value" ;;
    *) echo "Лишаю наявне ${key} (вже задано)" ;;
  esac
}

echo "== Генерація секретів у .env =="
maybe_set_kv OPENEDX_SECRET_KEY "$(rand_hex 32)"
maybe_set_kv MYSQL_ROOT_PASSWORD "$(rand_hex 24)"
maybe_set_kv MYSQL_PASSWORD "$(rand_hex 24)"
maybe_set_kv JWT_COMMON_SECRET_KEY "$(rand_hex 32)"
maybe_set_kv CMS_OAUTH2_SECRET "$(rand_hex 32)"
maybe_set_kv ADMIN_PASSWORD "$(rand_hex 16)"
maybe_set_kv AI_TUTOR_SHARED_SECRET "$(rand_hex 32)"

echo "== JWT RSA ключ (LMS<->CMS SSO) =="
if grep "^JWT_PRIVATE_SIGNING_JWK_JSON=" .env | grep -q "change-me"; then
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT
  ssh-keygen -t rsa -b 2048 -m PEM -f "$tmpdir/jwt_rsa" -N "" -q
  eval "$(python3 docker/init/pem_to_jwk.py "$tmpdir/jwt_rsa")"
  # PRIVATE_JSON / PUBLIC_JSON — однолійкові JSON без пробілів
  set_kv JWT_PRIVATE_SIGNING_JWK_JSON "$PRIVATE_JSON"
  set_kv JWT_PUBLIC_SIGNING_JWK_SET_JSON "$PUBLIC_JSON"
else
  echo "Лишаю наявні JWT JWK (вже задано)"
fi

echo "Готово. Перевір .env і впиши AI_TUTOR_LLM_API_KEY вручну."
