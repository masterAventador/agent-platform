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

`.env.example` 中的凭据只用于本机开发，生产环境必须由密钥服务提供。PostgreSQL、Redis 和 MinIO 的所有宿主机发布端口都固定绑定 `127.0.0.1`；平台容器继续通过受控 Docker 网络访问这些服务。

核心 Compose 的标准静态门禁会校验 Compose 语法以及全部发布端口的回环绑定契约：

```bash
bash infra/compose/test.sh config
```

该入口要求 Docker Compose `>= 2.20.0`；Docker Compose 缺失或版本过低时会明确失败。脚本按自身路径解析仓库根目录，因此用绝对路径调用时可从任意 cwd 执行，例如 `bash /path/to/ai-agent/infra/compose/test.sh config`。

## LiteLLM 本机模型网关

LiteLLM 使用独立的 `agent-platform-litellm` Compose project，包含官方 Proxy、仅内部可见的 PostgreSQL 和一次性 scoped-key bootstrap。它不依赖 core、RAGFlow 或 observability；Proxy 与平台 Worker 通过显式外部 bridge 网络 `agent-platform-llm` 互通。停止模型网关不会把其他栈识别为 orphan，也不会删除该外部网络。宿主机端口固定绑定 `127.0.0.1`，宿主机默认地址为 `http://127.0.0.1:4000`；容器内 Worker 默认使用服务 DNS `http://litellm:4000/v1`，不能用指向 Worker 自身的 `127.0.0.1`。

LiteLLM 镜像固定为官方非 root 变体 `ghcr.io/berriai/litellm-non_root:v1.86.2@sha256:511b513bc68956793433d62c1812daff56984325543f6a15431c622823fd90cb`，PostgreSQL 固定为 `postgres:17.10-alpine3.23@sha256:8189a1f6e40904781fc9e2612687877791d21679866db58b1de996b31fc312e4`；禁止改为 `latest`、RC、私有 Fork或本地修改的上游源码。LiteLLM 的固定 OCI image index 已确认同时包含 `linux/amd64` 和 `linux/arm64`，镜像 config 的默认 UID/GID 为 65534。仓库只通过 LiteLLM 官方 CLI、公开 YAML 配置和 HTTP API 使用它。

先幂等创建跨 project 网络，再准备只存于本机的环境文件。示例不包含可用 provider key；`LITELLM_MASTER_KEY`、`LITELLM_WORKER_API_KEY` 和数据库密码中的 `CHANGE_ME` 都必须替换。两个 `sk-...` 值必须分别在本机生成、足够随机且互不相同：master 只供 Proxy 和 bootstrap 管理使用，Worker 只持有 scoped virtual key。

```bash
bash infra/litellm/network.sh ensure
cp infra/compose/.env.litellm.example infra/compose/.env.litellm
export LITELLM_ENV_FILE="$PWD/infra/compose/.env.litellm"
```

bootstrap 会幂等创建或收敛 `agent-platform-worker` virtual key，只允许 `general-purpose` 模型以及 chat completions、models/readiness 路由。该 key 的 metadata 明确标记为本地共享 Worker 应用凭据；当前只能做应用级归因，不代表已实现租户级凭据隔离。每租户动态 key、预算、轮换和撤销需要后续单独实现。

业务侧只使用稳定模型 alias `general-purpose`。真实 provider 和模型由环境变量选择，避免 OpenAI、Anthropic、DashScope 或 Volcengine 等厂商名称进入平台业务配置：

```dotenv
# DashScope
LITELLM_UPSTREAM_MODEL=dashscope/qwen-plus
LITELLM_UPSTREAM_API_KEY=<your-local-dashscope-key>
LITELLM_UPSTREAM_API_BASE=

# Volcengine：把 endpoint-id 替换为火山方舟推理接入点 ID
LITELLM_UPSTREAM_MODEL=volcengine/<endpoint-id>
LITELLM_UPSTREAM_API_KEY=<your-local-volcengine-key>
LITELLM_UPSTREAM_API_BASE=
```

`LITELLM_UPSTREAM_API_BASE` 对以上两个示例可留空，只在自定义或 OpenAI-compatible 端点要求显式 base URL 时设置。该行为已经按固定版本 v1.86.2 的官方源码审计：环境变量空值被解析为 `""`，DashScope chat adapter 和 Volcengine adapter 都用 truthy fallback 回退各自默认 endpoint，因此空字符串不会覆盖默认地址；升级 LiteLLM 或换用其他 provider 时必须重新审计，不能直接泛化。当前 Demo 百炼 Key 由用户明确决定随私有仓库同步，以便多台开发电脑直接复用；该例外不得扩展到服务器私钥、生产凭据或客户凭据。健康检查只访问 `/health/liveliness`，不会调用上游模型或产生费用。

启动和停止只影响 LiteLLM project。PostgreSQL 不发布宿主机端口，数据保存在该 project 的命名 volume 中：

```bash
docker compose --env-file "$LITELLM_ENV_FILE" -f infra/compose/litellm.yml up -d --wait
docker compose --env-file "$LITELLM_ENV_FILE" -f infra/compose/litellm.yml down
```

可重复验收分为离线配置契约、远端镜像架构清单、真实短启健康检查、本地 stub completion、锁定 backend 宿主环境的 scoped-key readiness、正式 Worker 镜像 ChatOpenAI 链路和协议矩阵。每次动态验收都生成唯一的 `agent-platform-litellm-test-*` project、唯一 external 测试网络、随机回环端口、随机 master/worker/数据库凭据和独立 volume；退出 trap 会 `down -v` 本轮测试 project，再按严格名称 guard 删除本轮唯一测试网络，绝不会删除生产 `agent-platform-llm` 网络，也不会停止或污染用户正在运行的真实网关与数据库：

```bash
bash infra/litellm/test.sh config
bash infra/litellm/test.sh image-platform
bash infra/litellm/test.sh start-health
bash infra/litellm/test.sh stub-completion
bash infra/litellm/test.sh worker-readiness
bash infra/litellm/test.sh worker-chat
bash infra/litellm/test.sh stub-matrix
```

`config` 校验精确官方镜像 tag+index digest、Compose project 独立性、回环端口、只读配置挂载、CLI/健康端点、单一 `general-purpose` alias、外部网络、内部数据库、scoped-key bootstrap 和 env-only 凭据。`image-platform` 需要访问 GHCR，并要求镜像 manifest 同时含 `linux/amd64` 与 `linux/arm64`；`start-health` 会拉取当前主机架构镜像并验证真实 HTTP 健康端点，但不会发送模型请求。`worker-readiness` 和 `stub-matrix` 在 backend 锁定的宿主环境运行同一个 probe，经随机回环端口访问真实 LiteLLM：前者验证 scoped key 的精确模型/路由权限，后者覆盖 streaming、tool calls、structured output、retry/fallback 和 usage 字段。`worker-chat` 是唯一构建正式 backend Worker 镜像的测试，它从镜像导入生产 `LiteLLMChatModelFactory`，经 ChatOpenAI、LiteLLM 和本地 stub 完成调用。stub 的 usage 只证明协议字段透传，不代表真实 provider 成本或账单；这些测试也不验证 DashScope/Volcengine 的真实凭据、网络、配额或计费 API。

Proxy 使用镜像默认非 root 用户，并显式启用只读根文件系统、`cap_drop: ALL` 和 `no-new-privileges`；只有 `/tmp` 与 migration workspace 是 UID 65534、容量受限且 `noexec` 的 tmpfs。选择官方 `litellm-non_root` 变体是数据库运行时要求：同版本标准镜像与 `litellm-database` 镜像都把生成的 Prisma query-engine 路径固定在 `/root/.cache`，强制覆盖为非 root UID 后 migration CLI 虽可成功，runtime query-engine 却不可遍历；禁止通过强制 USER、root init、host bind cache 或修改上游绕过。非 root 变体把预生成 engine 放在默认用户可执行的 `/app/.cache`，并启用 Prisma offline mode。升级镜像必须重新核对默认 USER、生成路径并执行真实数据库迁移验收，不能只看静态 Compose。

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

### C03 本机 MVP Profile（默认 Stub）

无需准备真实模型密钥即可从仓库根目录启动当前 MVP 所需的完整本机栈：

```bash
bash infra/platform/mvp-profile.sh start
bash infra/platform/mvp-profile.sh health
bash infra/platform/mvp-profile.sh status
bash infra/platform/mvp-profile.sh stop
```

该入口统一编排 PostgreSQL、Redis、MinIO、LiteLLM、本地 OpenAI-compatible Stub、API、Dispatcher、Worker、Sandbox Controller、Sandbox Janitor 和 Web 客户端。它只使用固定的 LiteLLM 官方镜像及仓库现有 Stub overlay，不读取供应商密钥、不发起付费模型调用，也不会启动或管理 RAGFlow；知识链路留到 C07 的独立 Knowledge Profile。

首次启动会在 Git 忽略的 `.local/mvp-profile/agent-platform-mvp/` 下生成权限为本机用户私有的随机开发凭据。运行目录必须位于当前仓库 `.local/` 下、由当前用户持有且权限为 `0700`，四个环境文件必须是权限为 `0600` 的普通文件；路径中的符号链接、宽松权限、未知/重复 dotenv 键、非字面值、非法或重复端口以及非预期 Docker socket/网络配置都会在调用 Docker 前被拒绝。脚本不会 `source` 环境文件。同一 Profile 的所有操作由 `.local/mvp-profile-locks/` 下的原子锁串行化；干净启动还会在 Core、LiteLLM 和 App 各自执行 `up` 前分组预检对应回环端口，把端口选择到 Compose 绑定之间的竞态窗口压缩到单次预检与单次 `up` 之间。

每个工作树使用由仓库绝对路径派生的 backend/frontend 镜像标签，启动前都会构建当前工作树内容，不复用全局 `:local` 标签。backend runtime 显式安装 PostgreSQL 客户端运行库，确保生产 Worker 镜像能够加载锁定的 psycopg 实现。重复 `start`、`health` 和 `stop` 都是幂等操作；普通 `stop` 保留开发数据卷，若需要同时删除本 Profile 的卷，可显式执行：

```bash
MVP_PROFILE_REMOVE_VOLUMES=true bash infra/platform/mvp-profile.sh stop
```

启动阶段任何配置、容器或健康检查失败时，脚本会返回非零状态并报告清理是否完整：启动前会按稳定资源名快照同 Profile 的容器、网络和卷，失败时只按差集删除本轮新建资源，不执行破坏性的整体 `down`，因此启动前已经存在或被重启的资源会保留；外部 LiteLLM 网络同样只有在本轮新建且归属标签正确时才会删除。运行环境文件始终保留用于恢复和诊断。环境文件集合缺失但 Docker 中仍有同 Profile 容器、卷或网络时，`stop` 会明确返回非零并保留资源，禁止误报“已经停止”或在缺少可信配置时盲目清理。任何资源枚举、网络存在性/归属检查或删除失败都视为清理不完整，不会被静默吞掉；归属其他 Profile 的网络会原样保留并让停止操作失败，只有明确确认 LiteLLM 网络不存在时才按幂等停止处理。真实隔离验收使用随机项目名、一次分配的七个唯一回环端口和独立运行目录，覆盖 `status`、重复启停、保留卷后的失败重启与恢复、故障清理、同 Profile 并发拒绝、工作树镜像隔离、RAGFlow 排除以及最终容器/网络/卷无残留。验收还会用正式 Playwright 从注册、登录开始创建并发布员工、发起任务，经过生产 Dispatcher、Worker、Deep Agents、LiteLLM 和本地 Stub，核对 PostgreSQL 命令/事件持久化，并在页面刷新后确认终态仍可恢复。Playwright 只调用已经显式安装的本地二进制；依赖缺失时失败关闭，不在验收阶段隐式安装或改变供应链配置：

```bash
bash infra/platform/test-mvp-profile.sh
```

该验收同时用测试专用不可见窗口运行真实 Tauri 宿主中的核心业务链路，不会在 macOS
桌面弹出 App。无业务栈时，普通 `pnpm test:tauri` 只运行桌面能力与 Sidecar smoke，
完整核心流程按条件跳过；Windows 对应构建和 smoke 在 GitHub Actions 运行。

真实百炼请求必须显式运行，默认回归永远不会调用付费模型：

```bash
bash infra/litellm/test.sh real-provider
```

该命令使用项目现有 `.env.litellm`，拉起独立的临时 LiteLLM 和数据库，通过
`general-purpose` 稳定别名向百炼发送最小 `chat/completions` 请求，并校验真实 token
usage。输出不包含密钥、请求正文或原始供应商响应；结束后自动清理临时容器、网络和卷。

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

Worker 默认不启动。仓库内置正式 runtime adapter bundle，直接组合 SQLAlchemy 沙盒租约、Local Controller、Deep Agents 公共 Sandbox 校验、Tool 审计和本机凭据解析，不再要求用户提供 `module:attribute` 外部工厂。workflow/hybrid 只有命中平台已注册的 workflow ID+version 才能运行，空注册表明确失败关闭，不会伪造固定流程。启动 Worker 前必须配置 controller bearer secret，并先启动独立 LiteLLM 网关。Worker 不接收 OpenAI/Anthropic 等 provider key，也绝不能接收 LiteLLM master key；它只接收 provider-neutral 的网关 URL 与 scoped gateway key。请让 `.env.litellm` 的 `LITELLM_WORKER_API_KEY` 与 `.env.platform` 的 `AGENT_PLATFORM_LLM_GATEWAY_API_KEY` 使用同一个随机 `sk-...` 值，并确保两者都不同于 `.env.litellm` 的 `LITELLM_MASTER_KEY`。`--env-file` 不会把变量导出到当前 shell，因此不要用未显式导出的变量覆盖 platform 配置：

```bash
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
