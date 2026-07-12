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
