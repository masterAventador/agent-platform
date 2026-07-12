#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$(${SCRIPT_DIR}/manage.sh config)"

rg --color=never --quiet 'published: "13306"' <<< "${CONFIG}"
rg --color=never --quiet 'ES_JAVA_OPTS: -Xms512m -Xmx512m' <<< "${CONFIG}"
rg --color=never --quiet 'mem_limit: "1610612736"' <<< "${CONFIG}"

echo "RAGFlow Compose 配置校验通过"
