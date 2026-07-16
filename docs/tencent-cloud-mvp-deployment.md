# 腾讯云最小成本 MVP 部署基线

> 文档性质：当前腾讯云资源、MVP 部署拓扑、采购边界与后续扩容条件的正式基线
> 建立日期：2026-07-14
> 当前阶段：单机、无 RAGFlow、低并发演示
> 目标：以最小固定成本跑通可演示 MVP，不提前购买生产级高可用服务

## 1. 已确认的供应商决策

本项目采用“腾讯云基础设施 + 阿里云百炼模型服务”的组合：

- 模型服务：阿里云百炼，当前通过北京地域 OpenAI 兼容接口调用 `qwen-plus`；
- 业务对象存储：轻量对象存储（Lighthouse 版），必要时兼容普通 COS；
- 视频最终渲染：腾讯云媒体处理 MPS；
- 云端运行环境：腾讯云轻量应用服务器；
- 浏览器 RPA、Windows 微信 UI Automation、macOS Accessibility/AX 和本地 OCR：继续在用户设备的 Tauri Sidecar 执行，不迁移到云服务器。

腾讯云是本项目的云端运行、对象存储和视频处理供应商，不代表模型服务必须迁移到腾讯云。[`dt-ai-helper-competitive-analysis.md`](dt-ai-helper-competitive-analysis.md) 中的阿里云 OSS、Timeline Web SDK 和 IMS/ICE 是竞品事实，不代表本项目采用这些阿里云媒体产品；百炼模型服务是独立且已经确认的项目决策。竞品证据必须保留原貌；本项目的开发与采购以本文档和两份路线图为准。

## 2. 当前已有资源实测

实测日期：2026-07-14。

| 项目 | 当前结果 |
| --- | --- |
| 服务器类型 | 腾讯云轻量应用服务器 Lighthouse |
| 公网地址 | `49.233.213.109` |
| 地域 | 北京，`ap-beijing` |
| 操作系统 | Debian GNU/Linux 13（trixie） |
| 架构 | x86_64 |
| CPU | 4 vCPU，AMD EPYC 7K62 |
| 内存 | 3.6 GiB，可用约 2.9 GiB |
| Swap | 2 GiB |
| 系统盘 | 39 GiB，已用 7 GiB，可用 32 GiB |
| Docker | 尚未安装 |
| SSH | `root` + 本机 ED25519 公钥已验证成功 |
| 轻量对象存储挂载 | `/lhcos-data` 当前不存在 |

禁止把服务器密码、私钥、生产凭据或客户凭据写入本文档和 Git。当前有两个用户明确批准的 Demo 例外随私有仓库版本化：百炼 API Key，以及 2026-07-16 批准的开发用腾讯云子账号 `agent-platform-server` SecretId/SecretKey 与开发桶 `agent-platform-1424480216`（位于 `infra/compose/.env.platform`，仅限开发/演示，C18 时轮换废止）。不得把例外扩展到其他凭据。服务器地址不是认证凭据，但所有公网端口仍必须遵守最小暴露原则。

## 3. MVP 最小部署拓扑

暂不启用 RAGFlow 时，现有 4C4G 服务器作为唯一云端计算节点：

```text
Tauri App / Web 调试端
├── 浏览器 RPA：用户设备
├── Windows 微信 UIA/OCR：用户 Windows 设备
├── macOS 微信 AX/Vision OCR：用户 Mac 设备
└── 素材直传：STS → LighthouseCOS
              │
              ▼
腾讯云 Lighthouse 4C4G（北京）
├── Nginx + React 静态产物
├── FastAPI API
├── Dispatcher / Agent Worker
├── LiteLLM Proxy
├── PostgreSQL
├── Redis
├── MinIO（现有代码过渡期）
└── 轻量 OpenTelemetry/本地日志
              │
              ├── 阿里云百炼：对话、工具调用、结构化输出、Embedding
              ├── LighthouseCOS：素材、成片、Artifact、更新文件
              └── MPS：云端剪辑、合成、转码和异步 Job
```

本拓扑只面向单客户、少量测试账号和低并发演示。允许服务中断和演示数据丢失，不得直接作为正式生产环境交付。

## 4. 当前需要开通的腾讯云服务

| 服务 | MVP 用途 | 计费/采购策略 | 当前结论 |
| --- | --- | --- | --- |
| 已有 Lighthouse | API、Worker、LiteLLM 和基础依赖 | 复用现有实例 | 不新增服务器 |
| 阿里云百炼 | 语言模型和向量模型 | 现有北京地域 API Key，按量调用 | 已开通 |
| 轻量对象存储 | 素材、成片、Artifact、安装与更新文件 | 优先使用已有套餐，超额按量 | 不购买普通 COS |
| MPS | 视频剪辑、拼接、多轨合成、字幕、转场、混音、画中画和转码 | 开通后按任务计费，不买资源包 | 开发视频功能时启用 |
| CAM / STS | 服务端权限和客户端临时上传凭据 | 平台能力，无独立计算实例 | 必须配置 |
| TCR 个人版 | 暂存自研 Docker 镜像 | 免费限额版 | 可选 |
| VPC / 防火墙 | 网络隔离和端口控制 | 使用 Lighthouse 自带能力 | 必须配置 |

### 4.1 阿里云百炼使用边界

- LiteLLM 通过百炼北京地域 OpenAI 兼容接口接入，业务代码继续只调用稳定别名 `general-purpose`；
- 普通对话、AI 客服、内容生成和工作流先选择低成本通用模型；
- RAG 启用后，文本向量优先使用百炼的低成本 Embedding；
- MVP 不购买专属模型实例，不部署本地大模型，也不购买 GPU；
- 必须设置调用额度、超额拒绝和费用告警。

### 4.2 轻量对象存储使用边界

轻量对象存储（Lighthouse 版）底层基于 COS，并兼容 COS API、SDK、权限、CORS 和访问域名。北京服务器必须优先创建或复用 `ap-beijing` 的私有存储桶。

建议使用以下前缀：

```text
artifacts/   # AI 任务产物
materials/   # 视频、图片、音乐和模板素材
renders/     # MPS 最终成片
updates/     # Tauri 安装包和更新清单
diagnostics/ # 脱敏后的受控诊断产物
```

执行规则：

- 存储桶保持私有，下载使用短有效期签名 URL；
- Tauri 只接收服务端签发的限定目录、短有效期 STS 临时凭据；
- 永久 SecretId/SecretKey 只保存在服务端安全配置中；
- 不把 PostgreSQL、Redis、Docker Volume、LangGraph Checkpoint 或 RAGFlow 内部数据放在对象存储挂载目录；
- 应用通过 COS API/SDK 使用存储，不依赖 `/lhcos-data` 挂载；
- 正式采用前，必须完成 Tauri 直传、CORS、签名下载、删除和跨租户权限测试；
- MPS 对轻量对象存储的 COS 输入/输出必须完成真实小样；通过前保留普通 COS 按量桶作为兼容回退，不提前购买资源包。

### 4.3 MPS 使用边界

- `VideoRenderProvider` 的第一实现改为 `TencentMpsProvider`；
- MPS 负责最终 MP4 的云端异步生成，不在 4C4G 服务器运行完整 FFmpeg 批量渲染；
- 输入优先使用 LighthouseCOS/COS，成片写回 `renders/`；
- 保存 MPS Task ID、进度、费用、错误、重试、取消和最终对象 ID；
- App 关闭后任务继续，重新打开后恢复云端真实状态；
- MPS 没有被本项目确认具备与 `AliyunTimelinePlayer` 对等的 Web 时间线编辑器，因此 Timeline DTO、编辑 UI 和低清预览由 Tauri/React 自研；
- 第一阶段不购买云点播 VOD 或播放器 License，只有形成面向观众的视频播放业务后再评估。

## 5. 4C4G 运行边界

CPU 足以承载 MVP，内存和磁盘是主要约束。

| 组件 | 建议内存上限 |
| --- | --- |
| PostgreSQL | 400-600 MiB |
| Redis | 128-256 MiB |
| MinIO | 256-400 MiB |
| FastAPI | 400-600 MiB |
| Dispatcher / Worker | 600-900 MiB |
| LiteLLM | 200-400 MiB |
| Nginx / React | 50-100 MiB |
| 系统与 Docker 预留 | 700-1000 MiB |

MVP 强制约束：

- 同时最多 1-2 个 AI Run；
- Sandbox 并发限制为 1，只运行受控演示任务；
- 不启动 RAGFlow；
- 不运行本地大模型；
- 不运行本地 FFmpeg 批量渲染；
- 不在当前服务器同时运行完整 Prometheus、Grafana、Jaeger、Elasticsearch 等重型观测栈；
- Docker 日志必须轮转并限制单容器大小；
- 视频和大文件直传对象存储，禁止经 FastAPI 中转；
- 保留现有 2 GiB Swap，但 Swap 只能防止突发 OOM，不能代替内存；
- 系统盘可用空间低于 10 GiB 或镜像清理无法恢复时，优先扩容到 80 GiB，不先升级 CPU。

出现以下任一情况时升级到 4C8G：

- 内存持续超过 80%；
- 频繁使用 Swap；
- 出现容器 OOM；
- 同时运行多个数字员工或 Sandbox；
- 启用完整观测服务；
- 多个客户同时演示。

## 6. MinIO 过渡与 Artifact Provider

当前 Core 代码和本地 Compose 以 MinIO 为 Artifact 存储。为了先跑通现有 MVP，允许在 4C4G 上短期运行受限 MinIO，但不得让视频素材和成片长期堆积在 39 GiB 系统盘。

C04 实施时必须：

1. 定义供应商无关的 `ArtifactStorageProvider`；
2. 保留 `MinioArtifactProvider` 用于本地开发和测试；
3. 增加 `TencentCosArtifactProvider`，兼容 LighthouseCOS；
4. 将业务素材、成片和桌面更新文件迁移到对象存储；
5. 页面、API、员工和工作流只使用稳定 Artifact ID，不直接依赖 COS 对象键；
6. 验证租户隔离、STS、签名 URL、生命周期、失败清理和 Provider 切换。

C04 已完成 Provider 抽象、MinIO 真实闭环、腾讯云官方 COS SDK 适配、租户隔离和持久失败恢复；真实 LighthouseCOS/COS 桶仍必须通过显式 `TEST_COS_REGION`、`TEST_COS_SECRET_ID`、`TEST_COS_SECRET_KEY`、`TEST_COS_BUCKET` 门禁验证。STS、客户端直传、签名 URL 和生命周期策略属于后续云端/大文件链路，未提供真实云资源前不得标记为通过。

## 7. RAGFlow 延后采购方案

当前 MVP 不部署 RAGFlow，因此知识库解析、切片、检索和引用链路暂不作为演示门禁。

开发进入 C07 时再新增一台服务器：

| 项目 | 最小建议 |
| --- | --- |
| 实例 | 普通 x86_64 CPU 云服务器 |
| CPU | 4 vCPU |
| 内存 | 16 GiB |
| 硬盘 | 100 GiB SSD |
| 网络 | 与应用服务器同地域、内网互通 |
| 公网 | 仅用于安装和拉取镜像；RAGFlow API 不对公网开放 |

RAGFlow、MySQL、Elasticsearch、Redis 和 MinIO 先全部使用 RAGFlow 官方 Docker Compose 跑在这台机器上。即使同机，也必须保持 RAGFlow 自己的网络、Volume、凭据和端口，不得复用平台 PostgreSQL、Redis 或 MinIO。

MVP 和早期试运营阶段不单独购买腾讯云 MySQL、Redis、Elasticsearch 或对象存储实例。只有出现持续性能瓶颈、数据规模、备份恢复或正式 SLA 要求后，才评估外置托管服务。

## 8. 当前明确不购买的服务

- TKE / Kubernetes；
- CLB；
- 腾讯云托管 PostgreSQL、MySQL、Redis 和 Elasticsearch；
- 腾讯云向量数据库；
- 云点播 VOD 和播放器 License；
- CDN；
- WAF、付费 Anti-DDoS、云防火墙；
- SSM/KMS；
- CLS、托管 Prometheus 和 APM；
- 云备份、快照、跨地域容灾；
- GPU 云服务器；
- 腾讯云 OCR；
- 腾讯云智能体开发平台；
- CKafka / TDMQ；
- TCR 企业版；
- Windows 云服务器和云桌面；
- 短信、邮件服务。

以上服务不是永久禁止。只有对应业务量、合规、可用性或运维指标达到明确阈值，并经过用户确认后才能采购。

## 9. 自动运营的云端边界

| 功能 | 执行位置 | 腾讯云新增采购 |
| --- | --- | --- |
| 抖音、小红书、快手和视频号发布 | 用户设备 Tauri + Playwright Sidecar | 无 |
| 扫码登录和 Cookie 健康检查 | 用户设备，服务端只管理授权状态 | 无 |
| PC 微信 UIA / OCR | 用户 Windows 电脑 | 无 |
| macOS 微信 AX / Vision OCR | 用户 Mac；ScreenCaptureKit 只截取授权窗口 | 无 |
| 微信自动回复和抖音 AI 客服 | 本地执行 + 阿里云百炼内容生成 | 仅模型用量 |
| 自动曝光、定向曝光和主动私信 | 用户设备受控执行 | 无 |
| 调度、审批、审计和人工接管 | 现有应用服务器 | 无新实例 |

禁止为了 24 小时运行而默认购买 Windows 云服务器。微信 UI 自动化依赖真实桌面会话、分辨率、DPI/缩放、窗口和锁屏状态，优先使用客户授权的专用 Windows 或 macOS 设备。Windows 与 macOS 共用上层协议但使用独立 Adapter；客户端缺少入口的功能不得宣称跨平台等价。

## 10. 上线前再补的生产能力

以下能力延后到正式客户上线前：

- 数据库、对象、配置和凭据备份；
- HTTPS 正式域名、备案和证书自动续期；
- 凭据管理和自动轮换；
- CLS、Metrics、Trace、告警和费用监控；
- WAF、限流、入侵防护和安全扫描；
- 生产 Sandbox 隔离节点；
- CLB、多实例、滚动升级和回滚；
- 数据保留、删除、隐私和客户导出机制；
- macOS/Windows 安装包签名、公证和自动更新。

## 11. 开发任务映射

| 路线图任务 | 腾讯云落地 |
| --- | --- |
| C03 | 完整栈先排除 RAGFlow，通过 LiteLLM 完成百炼真实 AI 烟测 |
| C04 | `ArtifactStorageProvider` + `TencentCosArtifactProvider` |
| C07 | 新增 4C16G RAGFlow 节点后实施 |
| C14 | MVP 使用轻量观测，正式阶段再评估 CLS 等托管服务 |
| C16 | 百炼模型别名、质量、成本和配额治理 |
| C18 | 腾讯云 CAM/STS、生产凭据和 Sandbox 隔离 |
| C20 | Lighthouse 部署、LighthouseCOS 更新产物和正式发布流程 |
| B04 | LighthouseCOS 素材库、STS 直传和下载任务 |
| B05 | 自研 Timeline DTO、编辑器和 App 内低清预览 |
| B06 | `TencentMpsProvider`、真实 EditMedia 小样和成片回写 |
| B07 | 从 LighthouseCOS Artifact 进入多平台发布 |
| B09-B16 | 阿里云百炼 + 用户设备本地执行器，不新增云主机 |

## 12. MVP 验收清单

- [ ] 服务器安装并锁定 Docker Engine 与 Compose 版本；
- [ ] 配置容器内存限制、日志轮转和健康检查；
- [ ] 只开放必要公网端口，SSH 仅使用密钥；
- [x] 阿里云百炼通过 LiteLLM 完成最小真实请求（2026-07-15，`qwen-plus`，23 Token）；
- [x] 创建北京私有 LighthouseCOS 存储桶（2026-07-16：`agent-platform-1424480216`，私有读写；CAM 子账号 `agent-platform-server` 已授权 COS+STS）；
- [ ] 完成服务端 STS、Tauri 直传和签名下载（凭据已就绪，签发实现属 B04，待 C17 三层授权）；
- [x] 验证现有 MinIO 路径和 Tencent COS Provider 迁移边界（2026-07-15：MinIO 真实全链路及 COS 官方 SDK 契约通过；2026-07-16：真实 COS 桶凭据门禁通过，并修复 Provider 对真实 StreamBody 的资源释放缺陷——Mock 曾放宽 close 契约）；
- [ ] MPS 从 LighthouseCOS/COS 读取素材并将成片写回；
- [ ] 完成单用户、单 Worker、单 Sandbox 的资源压力检查；
- [ ] 验证服务器重启后容器恢复；
- [ ] 确认关闭 RAGFlow 后 Core 非知识库流程不受影响；
- [ ] 记录实际 Token、对象存储、MPS 和服务器费用。

## 13. 官方参考

- [腾讯云轻量对象存储（Lighthouse 版）](https://cloud.tencent.com/product/lighthousecos)
- [轻量对象存储产品概述](https://cloud.tencent.com/document/product/1207/80957)
- [轻量对象存储挂载说明](https://cloud.tencent.com/document/product/1207/97692)
- [COS 临时密钥生成及使用](https://cloud.tencent.com/document/product/436/14048)
- [腾讯云 MPS 编辑视频 API](https://cloud.tencent.com/document/product/862/43010)
- [阿里云百炼与 LangChain 集成](https://help.aliyun.com/zh/model-studio/use-bailian-in-langchain)
- [阿里云百炼文本向量接口](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)
- [RAGFlow 官方部署要求](https://github.com/infiniflow/ragflow/blob/main/README.md)
- [RAGFlow 官方 Docker 依赖说明](https://github.com/infiniflow/ragflow/blob/main/docker/README.md)
- [Apple AXUIElement](https://developer.apple.com/documentation/applicationservices/axuielement_h)
- [Apple ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit)
- [Apple Vision 文字识别](https://developer.apple.com/documentation/vision/recognizing-text-in-images)
