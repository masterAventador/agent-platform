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
docker compose --env-file infra/compose/.env -f infra/compose/core.yml up -d
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

版本依据：Collector 使用 [OpenTelemetry 官方 `v0.156.0` release](https://github.com/open-telemetry/opentelemetry-collector-releases/releases/tag/v0.156.0)（2026-07-07）；Jaeger 使用仍发布 `jaegertracing/all-in-one` 官方镜像的 [Jaeger `v1.76.0` release](https://github.com/jaegertracing/jaeger/releases/tag/v1.76.0)。Jaeger 2.x 已改用 `jaegertracing/jaeger` 镜像，因此本地 all-in-one 基线保持在 1.76.0，升级时需单独评估镜像和配置迁移。
