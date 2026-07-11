# AI Agent Platform 项目规则

## Web / Tauri 平台适配规范（强制）

**核心规则：** Web 和 Tauri 桌面端共用同一套 React 业务页面、组件、API、状态管理和数据模型。所有浏览器与桌面平台存在差异的能力，必须通过统一的 `PlatformAdapter` 接口访问。

### 适配器结构

项目必须保持类似以下结构：

```text
platform/
├── types.ts       # PlatformAdapter 接口和公共类型
├── web.ts         # 浏览器实现
└── tauri.ts       # Tauri 实现
```

`PlatformAdapter` 至少负责以下平台相关能力：

- 保存、打开和选择文件；
- 在系统文件管理器中显示文件；
- 系统通知；
- 打开外部链接；
- 安全凭据读写；
- 自动更新检查；
- 窗口、托盘和桌面端专属能力。

### 禁止事项

- 业务页面、业务组件、Store 和普通 API 模块禁止直接导入 `@tauri-apps/*`；
- 禁止在业务代码中直接访问 `window.__TAURI__`；
- 禁止在各页面散落 `isTauri`、User-Agent 或运行环境判断；
- 禁止分别维护两套 Web/Tauri 业务页面；
- 禁止把长期Token或企业敏感凭据直接存入 `localStorage`；
- 禁止为了桌面端功能破坏 Web 端的标准 HTTP/SSE 接口。

### 正确做法

- 运行环境判断集中在平台适配器初始化处；
- 业务代码只依赖 `PlatformAdapter`，不感知具体运行平台；
- Web 实现使用浏览器标准 API；
- Tauri 实现使用 Tauri Command、Plugin、Channel 和 Capability；
- 不支持的能力必须返回明确的能力状态或受控错误，不允许静默失败；
- 新增平台能力时先扩展公共接口，再分别实现 Web 和 Tauri 版本；
- 两个实现必须具有统一的输入、输出和错误语义；
- 平台适配器需要独立测试，核心能力应具有 Web/Tauri 契约测试。

### 复用边界

以下内容应在 Web 和 Tauri 中直接复用：

- React 页面和业务组件；
- Ant Design / Ant Design X 业务封装；
- Axios、TanStack Query 和 SSE 客户端；
- Zustand 状态；
- Zod Schema；
- 权限、任务、审批、知识库、Skill 和数字员工业务逻辑。

以下内容允许通过 `PlatformAdapter` 分平台实现：

- 文件系统和系统文件选择器；
- OIDC 登录回调和 Deep Link；
- 安全凭据存储；
- 系统通知；
- 自动更新；
- 窗口、菜单、托盘和全局快捷键；
- 桌面端原生流式通道。

### 后端边界

Tauri 只承载桌面客户端和必要的原生适配，不默认内置 Python Agent 后端。Web、Tauri 和后续 App 统一通过 HTTPS、SSE 或 WebSocket 访问云端 FastAPI 平台。只有明确提出离线执行需求并完成安全与升级设计后，才能引入本地 Sidecar。
