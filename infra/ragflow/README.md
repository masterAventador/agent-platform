# RAGFlow 独立运行栈

平台固定使用官方稳定版 `v0.25.6`。`manage.sh` 会把对应官方 Git tag 浅克隆到被 Git
忽略的 `.local/ragflow/`，以官方 Docker Compose 为基线运行。仓库内的
`compose.override.yml` 只覆盖宿主机端口和本机 Elasticsearch 资源上限，不复制、修改或
替换 RAGFlow 源码、官方 Compose 或内部依赖。

```bash
infra/ragflow/manage.sh prepare
infra/ragflow/manage.sh pull-image
infra/ragflow/manage.sh up
infra/ragflow/manage.sh status
infra/ragflow/manage.sh config
infra/ragflow/manage.sh down
bash infra/ragflow/test-manage.sh
```

RAGFlow API 地址为 `http://127.0.0.1:19380`，Web 管理界面为
`http://127.0.0.1:18080`。首次运行后在 RAGFlow 中创建 API Key，
通过 `AGENT_PLATFORM_RAGFLOW_API_KEY` 提供给平台后端。Apple Silicon 上若镜像仓库
只返回 AMD64 架构，Docker Desktop 会通过仿真运行对应容器，首次启动会明显更慢。

管理脚本通过官方 Compose 环境变量设置独立宿主机端口：MySQL `13306`、Valkey
`16379`、MinIO `19000/19001`、Elasticsearch `19200`。平台不得连接这些内部端口。
宿主机端口被其他进程占用（如常驻开发栈、SSH 隧道）时，可用 `RAGFLOW_*_PORT`
环境变量覆盖默认值，例如 `RAGFLOW_REDIS_PORT=26379 RAGFLOW_MINIO_PORT=29000
RAGFLOW_MINIO_CONSOLE_PORT=29001 RAGFLOW_WEB_HTTP_PORT=28080 manage.sh up`；
`down`/`status` 也要携带同一组变量。

默认启用官方 `tei-cpu` profile 提供本地 embedding（TEI 镜像预置模型，默认
`BAAI/bge-m3` 多语言模型；官方默认的 `Qwen/Qwen3-Embedding-0.6B` 在本机 CPU
warmup 需要 16GB 以上内存，不作默认，可用 `RAGFLOW_TEI_MODEL` 覆盖）。不启用
TEI 时 RAGFlow 没有默认 embedding 模型，文档解析会以 "No default embedding
model is set" 失败。profiles 可用 `RAGFLOW_COMPOSE_PROFILES` 覆盖。

本机覆盖同时将 Elasticsearch JVM 堆固定为 512MB、容器上限设为 1.5GB。完整运行
RAGFlow、TEI、Elasticsearch 及平台基础设施时，Docker/Colima 虚拟机至少应
分配 8GB 内存；本项目当前开发机使用 8 核、16GB。正式环境必须按实际容量独立配置，
不复用该开发值。

Docker Hub 下载不稳定时，可使用该官方 tag 的 `.env` 中列出的 Infiniflow 镜像源：

```bash
RAGFLOW_IMAGE_SOURCE=swr.cn-north-4.myhuaweicloud.com/infiniflow/ragflow:v0.25.6 \
  infra/ragflow/manage.sh pull-image
```

升级只能修改 `VERSION` 到新的官方稳定 tag，并执行项目规则要求的完整兼容回归；禁止在
`.local/ragflow/` 内维护补丁或把该目录提交到仓库。
