# Infrastructure

保存 PostgreSQL、Redis、MinIO、RAGFlow、OpenTelemetry 和监控组件的本地及部署配置。开发依赖按需启动，使用后停止。

## 核心本地依赖

```bash
cd infra/compose
cp .env.example .env
docker compose --env-file .env -f core.yml up
```

结束开发后：

```bash
docker compose --env-file .env -f core.yml down
```

`.env.example` 中的凭据只用于本机开发，生产环境必须由密钥服务提供。

## 本机链路追踪

观测栈由独立 Compose project 提供，可与核心依赖并行启动，停止观测栈不会把 PostgreSQL、Redis 或 MinIO 当成 orphan 清理。它只接收 OTLP traces；metrics 和 logs 尚未配置导出。

- OpenTelemetry Collector Contrib：`0.156.0`
- Jaeger all-in-one：`1.76.0`
- OTLP gRPC：`127.0.0.1:4317`
- OTLP HTTP：`127.0.0.1:4318`
- Collector health：`http://127.0.0.1:13133/`
- Jaeger UI：`http://127.0.0.1:16686/`

默认端口可分别通过 `OTEL_GRPC_PORT`、`OTEL_HTTP_PORT`、`OTEL_HEALTH_PORT` 和 `JAEGER_UI_PORT` 覆盖。所有发布端口都固定绑定回环地址，Jaeger 使用内存存储，停止容器后 trace 数据即丢失；配置中没有生产密钥。

只启动观测栈：

```bash
docker compose -f infra/compose/observability.yml up -d
```

与核心依赖并行启动：

```bash
docker compose --env-file infra/compose/.env -f infra/compose/core.yml up -d --wait
docker compose -f infra/compose/observability.yml up -d
```

使用后停止本轮启动的服务：

```bash
docker compose -f infra/compose/observability.yml down
```

静态契约、Compose 语法和 Collector 原生配置校验：

```bash
bash infra/observability/test.sh config
```

两个官方镜像都是不含 shell、curl 或 wget 的最小镜像，Compose 内不能伪装成 HTTP 探针。短暂启动后应由宿主机检查 Collector 与 Jaeger 的真实 HTTP 健康端点；以下脚本无论成功失败都只对独立观测 project 执行 `down`：

```bash
bash infra/observability/test.sh start-health
```

## API、Worker 与 Web 本机容器

平台进程使用独立的 `agent-platform-app` Compose project，不会把 core、RAGFlow 或观测容器当作 orphan 管理。它加入 core 创建的外部网络 `agent-platform_default`，通过 `postgres`、`redis`、`minio` 服务 DNS 访问核心依赖；RAGFlow 与 OTLP 使用显式的 `host.docker.internal`/`host-gateway`。因此 core 的宿主机端口可以保持只绑定 `127.0.0.1`，平台也能与三个独立 Compose 栈安全并行；platform `down` 不会删除外部 core 网络，平台 API 与 Web 端口同样只绑定 `127.0.0.1`。

先准备只存于本机的运行环境文件，并替换全部 `CHANGE_ME`：

```bash
cp infra/compose/.env.platform.example infra/compose/.env.platform
export PLATFORM_ENV_FILE="$PWD/infra/compose/.env.platform"
```

启动平台前必须先启动 core，使外部网络与三个服务可用：

```bash
docker compose --env-file infra/compose/.env -f infra/compose/core.yml up -d
```

平台使用同一个后端镜像运行一次性 Alembic migration、API、outbox Dispatcher 和 Worker。API 与 Dispatcher 只会在 migration 成功后启动；API 创建任务时把 Run 与命令原子写入 PostgreSQL，独立 Dispatcher 随后自动把待投递命令送入 Redis Stream。Dispatcher 当前强制单副本，并用 ready 文件提供容器健康语义；PostgreSQL 查询同时使用 `FOR UPDATE SKIP LOCKED`，但在引入带租约的 durable claim 前不得扩为多副本。Web 使用非 root NGINX 在 `8080` 提供静态资源，并将 `/api/` 反代到 API 服务。基础镜像固定 tag 与多架构 manifest digest：Python `3.12.13-slim-bookworm`、uv `0.11.28`、Node `24.13.0-alpine3.23` 与 NGINX unprivileged `1.28.0-alpine3.21`，均已在本机验证为 Linux arm64。版本与平台依据来自 [Python 官方镜像](https://hub.docker.com/_/python)、[Node 官方镜像](https://hub.docker.com/_/node)、[uv 官方 Docker 指南](https://docs.astral.sh/uv/guides/integration/docker/) 和 [NGINX 官方 unprivileged 镜像](https://hub.docker.com/r/nginxinc/nginx-unprivileged)。

按阶段验收：

```bash
bash infra/platform/test.sh contract
bash infra/platform/test.sh config
bash infra/platform/test.sh build
bash infra/platform/test.sh start
bash infra/platform/test.sh health
```

Worker 默认不启动。仓库内置正式 runtime adapter bundle，直接组合 SQLAlchemy 沙盒租约、Local Controller、Deep Agents 公共 Sandbox 校验、Tool 审计和本机凭据解析，不再要求用户提供 `module:attribute` 外部工厂。workflow/hybrid 只有命中平台已注册的 workflow ID+version 才能运行，空注册表明确失败关闭，不会伪造固定流程。启动 Worker 前必须配置 controller bearer secret；真实模型提供商密钥只注入 Worker，示例文件不会伪造模型密钥：

```bash
export ANTHROPIC_API_KEY='replace-with-local-secret'
docker compose --env-file "$PLATFORM_ENV_FILE" \
  -f infra/compose/platform.yml --profile worker up -d sandbox-controller sandbox-janitor worker
```

本机凭据文件默认关闭。只有显式配置 `AGENT_PLATFORM_LOCAL_CREDENTIALS_FILE` 时，才允许同时显式配置 `AGENT_PLATFORM_LOCAL_CREDENTIALS_REPOSITORY_ROOT`；宿主机直接运行不能假设仓库位于 `/app`。未来在 Compose 中启用凭据文件时，必须将 repository root 明确设为容器内代码根 `/app`，并把凭据文件只读挂载到 `/app` 之外；不得依赖应用默认值。

`sandbox-controller` 与 Worker 使用同一个 opt-in profile，但它是独立进程，也是唯一允许挂载 Docker socket 的本机特权边界。Worker 保持非 root、没有 socket、不能调用 Docker CLI，只能通过不发布宿主端口的内部网络和共享 bearer secret 调用 controller。controller 强制固定 digest 的 Linux arm64 镜像、非 root sandbox、只读根文件系统、独立 `/workspace`/`/skills` tmpfs、断网、移除全部 capability、`no-new-privileges` 和 CPU/内存/PID 限制。该实现仅用于受控的本机开发与 CI，不是公网多租户生产隔离基线；生产环境后续使用 E2B 等独立供应商。

Worker 以小于租约 TTL 的间隔为所有活跃 run 续租；续租失败会把 run 稳定标记为失败并停止安全执行。单副本 `sandbox-janitor` 原子 claim 所有到期的非终态租约（`PROVISIONING`、`ACTIVE`、`DELETING`、`ERROR`）：有 `sandbox_id` 时执行幂等 provider delete，无 ID 时按 canonical lease UUID 精确发现并删除。Controller 对同一 lease 的 create/find/delete 串行化；即使发现 0 个容器也写入带 epoch 的一小时 tombstone，拒绝已经排队的旧 generation create，过期 tombstone 与空闲 lease lock 会自动清理。发现多个精确匹配时 fail closed 并报警，绝不跨租约删除。PostgreSQL 行锁、`SKIP LOCKED` 和租约 fencing epoch/CAS 防止续租、创建完成、正常关闭与清理发生 lost update。Worker 收到 SIGTERM 后会 best-effort 释放全部运行环境并关闭 controller HTTP client，某个清理失败不会阻断其余环境释放。

Worker 的瞬时投递错误使用 Redis Consumer Group 的持久 delivery count 计数，默认最多尝试 5 次；该计数不会因 Worker 进程重启而归零。达到上限后，以 PostgreSQL `run_dead_letters` 为真相源执行可靠的两阶段结算，而不宣称死信记录与业务收敛跨事务原子：平台先耐久写入脱敏死信；ownership 可用时，再在独立结算事务内锁定 Run 行，必要时将原任务标记失败、将原命令标记已处理，并写入 `settled_run_id`。若存活的其他 Worker 仍持有 ownership，死信保持待结算且不 ACK Redis 原消息，后续按同一 delivery 幂等重入；只有死信已经结算，或畸形消息无法通过受限字段交叉验证到原 Run/Command 时才 ACK。Redis `agent-platform:runs:dlq` 仅是可补偿、幂等的运维镜像，不是重放真相源；镜像故障不会阻塞业务收敛，Worker 会依据 `mirrored_at` 后续补发。重放始终克隆出新的 Run 与 START 命令并交给 Dispatcher，禁止向终态旧 Run 塞入新命令。可通过 `AGENT_PLATFORM_QUEUE_MAX_DELIVERY_ATTEMPTS` 和 `AGENT_PLATFORM_RUN_QUEUE_DEAD_LETTER_STREAM_NAME` 覆盖安全默认值。

需要区分宿主机直接运行测试和 Compose 容器两层路径。宿主机 Python Docker SDK 不会自动读取 CLI 的当前 context，因此 opt-in 测试脚本会按下面方式设置 `DOCKER_HOST`：

```bash
export DOCKER_HOST="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
```

Compose bind mount 的 source 由 Docker daemon 解析。Colima daemon 位于 Linux VM 内，因此 `DOCKER_SOCKET_PATH` 应保持 `/var/run/docker.sock`，不能填宿主机 `~/.colima/.../docker.sock`；只有 daemon 侧 socket 确实位于其他路径时才覆盖该值。

真实安全回归会创建并最终删除一个临时 sandbox，覆盖 8 MiB/空文件/二进制上传下载、Skill 路径、命令超时、断网、资源硬化与无残留；默认 pytest 不执行该破坏性用例：

```bash
bash infra/platform/test-local-sandbox.sh
```

真实运行时浏览器回归使用隔离数据库、Redis DB 和专用进程 ready 标记，完整经过注册登录、员工发布、Dispatcher、Redis、Worker、Deep Agents 公共 API、本机 arm64 Docker sandbox、事件持久化和 UI 输出，并在退出时只清理本轮资源：

```bash
bash infra/platform/test-runtime-e2e.sh
```

该回归中的 `GenericFakeChatModel` 只存在于 `backend/tests/fixtures/runtime_worker.py`，通过公开模型注入 seam 避免调用付费模型；生产代码没有 Fake 开关。它验证的是真实平台运行链路与沙盒集成，不是 OpenAI、Anthropic 等外部模型 provider 的凭据、网络或 API 兼容性验收。

容器部署的正式 Playwright smoke 不会自行启动 dev server，目标地址通过环境变量传入；它以真实浏览器完成注册登录、同源 `/api` 调用和 SPA 深链接刷新：

```bash
cd frontend
PLATFORM_E2E_BASE_URL=http://127.0.0.1:8080 \
  pnpm exec playwright test --config playwright.deployed.config.ts
```

停止本轮平台容器，不影响其他 Compose project：

```bash
docker compose --env-file "$PLATFORM_ENV_FILE" -f infra/compose/platform.yml down
```

版本依据：Collector 使用 [OpenTelemetry 官方 `v0.156.0` release](https://github.com/open-telemetry/opentelemetry-collector-releases/releases/tag/v0.156.0)（2026-07-07）；Jaeger 使用仍发布 `jaegertracing/all-in-one` 官方镜像的 [Jaeger `v1.76.0` release](https://github.com/jaegertracing/jaeger/releases/tag/v1.76.0)。Jaeger 2.x 已改用 `jaegertracing/jaeger` 镜像，因此本地 all-in-one 基线保持在 1.76.0，升级时需单独评估镜像和配置迁移。
