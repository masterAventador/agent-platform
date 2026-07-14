# Tauri 客户端测试策略

> 文档性质：Tauri、共享 React UI 与平台适配器的强制测试基线
> 建立日期：2026-07-14
> 适用范围：`frontend/`、`frontend/src-tauri/`、桌面 Sidecar 接入和所有共享业务页面

## 1. 目标

Tauri 是普通用户的主要交付入口，但 Web 与 Tauri 共用同一套 React 页面和业务逻辑。测试体系必须同时保证日常回归速度、共享 UI 正确性和真实桌面原生能力，不允许只测浏览器后宣称桌面功能完成，也不为桌面端复制一套业务页面。

React Web 构建长期保留为自动化测试、内部调试和未来可选管理端入口。它是测试面，不代表用户需要在 App 与网页之间切换。

## 2. 四层测试模型

| 层级 | 工具 | 负责范围 | 不负责范围 |
| --- | --- | --- | --- |
| 单元与组件 | Vitest | React 组件、Store、Zod Schema、业务逻辑、平台契约和测试替身 | 真实浏览器流程、Rust IPC、系统能力 |
| Web 业务 E2E | Playwright | 共享 React UI 的页面访问、点击、输入、提交、权限和最终业务结果 | 真实 Tauri 窗口和操作系统能力 |
| Tauri 原生逻辑 | Rust `cargo test` | Command、权限、配置、序列化、错误转换、原生适配器和无需 WebView 的逻辑 | 完整 UI 与 WebView 交互 |
| Tauri 桌面 E2E | WebdriverIO + `@wdio/tauri-service` | 真实 App 启动、WebView、IPC、窗口和代表性原生桥接流程 | 大量可由 Playwright 更快覆盖的重复页面组合 |

`agent-browser` 可用于 Web 入口的探索、临时复现和人工式检查，但不计入正式自动化验收，也不用于证明真实 Tauri 原生能力。

## 3. 推荐目录与命令

C02 已按以下结构落地；确需调整名称时必须同步修改本文件、脚本和 CI：

```text
frontend/
├── src/
│   └── platform/
│       ├── types.ts
│       ├── web.ts
│       └── tauri.ts
├── e2e/                  # Playwright 共享 Web 业务流程
├── e2e-tauri/            # WebdriverIO 真实桌面流程
├── wdio.conf.ts
└── src-tauri/
    ├── src/
    └── tests/            # Rust 集成测试
```

已落地命令：

```bash
cd frontend
pnpm test
pnpm e2e
pnpm test:tauri
pnpm tauri:test:rust
pnpm typecheck
pnpm lint
pnpm build
pnpm tauri build
```

macOS 与 Windows 桌面编译和真实冒烟由
`.github/workflows/tauri-desktop.yml` 的双平台矩阵执行。测试构建通过
`desktop-test` Rust Feature 和 `tauri.test.conf.json` 显式启用驱动，正式构建不启用该 Feature。

## 4. PlatformAdapter 验收

每个 `PlatformAdapter` 能力必须具备统一的输入、输出、错误码和能力探测语义，并至少覆盖：

- Web 实现的契约测试；
- Tauri 实现的 Rust 或 TypeScript 契约测试；
- 不支持能力的受控错误；
- 用户取消、权限拒绝、文件不存在、IPC 失败和超时；
- 业务组件不直接导入 `@tauri-apps/*`，不读取 `window.__TAURI__`；
- 真实桌面 E2E 中至少有一个成功路径和一个关键失败路径。

文件选择、保存、系统通知、安全凭据、外部链接、窗口和自动更新等能力不能仅通过 Mock 标记完成。

## 5. 测试构建安全

- WebdriverIO 内嵌 WebDriver 服务只允许在测试构建或显式测试 Feature 中编译启用；
- 正式安装包必须验证不包含测试端口、测试命令、调试权限和 WebDriver 服务；
- 测试凭据使用隔离临时值，禁止复用开发、演示或生产凭据；
- 文件系统用例只操作测试临时目录，结束后必须清理；
- 系统通知、外链、文件选择等可能影响桌面的测试必须使用明确标识的测试数据；
- 原生测试失败不得降级为只跑 Web 测试后放行。

## 6. 日常开发与 CI 分层

日常共享 UI 改动至少运行相关 Vitest、Playwright、Typecheck 和 Lint。只改 Rust 或原生桥接时至少运行相关 Rust 测试、PlatformAdapter 契约测试和 Tauri 桌面 E2E。跨层功能同时运行两组。

持续集成按以下层级执行：

1. 每次提交：Vitest、Typecheck、Lint、Web Build、Rust 测试；
2. 影响业务流程的提交：追加相关 Playwright；
3. 影响 Tauri 或 PlatformAdapter 的提交：追加 WebdriverIO Tauri E2E；
4. 发布候选：macOS 与 Windows 分别构建安装包并运行桌面冒烟；
5. 阶段验收：运行完整 Playwright、完整 Tauri E2E 和全部静态门禁。

macOS 本机负责日常桌面验证；Windows 专属行为必须在 Windows CI 或真实 Windows 设备验证，不能用 macOS 编译成功替代。

## 7. 完成判定

- 纯共享页面功能：Vitest、Playwright、Typecheck、Lint 和 Build 全部通过；
- 纯原生能力：Rust 测试、PlatformAdapter 契约测试、WebdriverIO 桌面 E2E 和目标平台构建全部通过；
- 同时涉及页面与原生桥接：以上两类门禁全部通过；
- 只有 Web Mock 通过、桌面驱动无法运行、测试插件进入正式包或目标平台未验证时，一律不得标记完成；
- 当前任务实际执行的命令、平台和结果必须记录到对应路线图完成记录。

## 8. 官方依据

- Tauri Tests：<https://v2.tauri.app/develop/tests/>
- Tauri WebDriver：<https://v2.tauri.app/develop/tests/webdriver/>

截至 2026-07-14，Tauri 官方推荐通过 WebdriverIO 的 Tauri Service 覆盖 Windows、Linux 和 macOS；直接使用原生 `tauri-driver` 的平台范围与内嵌驱动不同。实现时锁定具体版本，并在升级时重新核对官方支持矩阵。
