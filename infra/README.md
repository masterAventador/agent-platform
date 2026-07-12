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

平台使用同一个后端镜像运行一次性 Alembic migration、API 和 Worker。API 只会在 migration 成功后启动，避免多个 API 实例同时执行迁移；Web 使用非 root NGINX 在 `8080` 提供静态资源，并将 `/api/` 反代到 API 服务。基础镜像固定 tag 与多架构 manifest digest：Python `3.12.13-slim-bookworm`、uv `0.11.28`、Node `24.13.0-alpine3.23` 与 NGINX unprivileged `1.28.0-alpine3.21`，均已在本机验证为 Linux arm64。版本与平台依据来自 [Python 官方镜像](https://hub.docker.com/_/python)、[Node 官方镜像](https://hub.docker.com/_/node)、[uv 官方 Docker 指南](https://docs.astral.sh/uv/guides/integration/docker/) 和 [NGINX 官方 unprivileged 镜像](https://hub.docker.com/r/nginxinc/nginx-unprivileged)。

按阶段验收：

```bash
bash infra/platform/test.sh contract
bash infra/platform/test.sh config
bash infra/platform/test.sh build
bash infra/platform/test.sh start
bash infra/platform/test.sh health
```

Worker 默认不启动。当前入口要求 `AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY=module:attribute` 指向真实 adapter factory，并会在 concrete runtime resolver、模型、workspace/sandbox、Skill 或 Tool Gateway 装配缺失时 fail-fast；仓库尚无可填写的 concrete adapter，Compose 不伪造路径或 Worker 健康状态。只有这些装配已就绪，并且真实模型提供商密钥已从宿主机环境导出后，才显式启用 profile；示例文件不会伪造模型密钥：

```bash
export ANTHROPIC_API_KEY='replace-with-local-secret'
export AGENT_PLATFORM_RUNTIME_ADAPTER_FACTORY='your_package.bootstrap:create_runtime_adapters'
docker compose --env-file "$PLATFORM_ENV_FILE" \
  -f infra/compose/platform.yml --profile worker up -d worker
```

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
