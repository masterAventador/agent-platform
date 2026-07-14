# Agent Platform Frontend

平台前端使用 React、TypeScript、Vite、Ant Design 和 Ant Design X。Web 与 Tauri 共用同一套业务页面，通过 `PlatformAdapter` 隔离平台能力。

```bash
pnpm install
pnpm dev
pnpm test
pnpm build
```

桌面端与 Web 复用上述 React 应用。平台差异只能通过 `src/platform/` 的
`PlatformAdapter` 访问；Tauri 原生代码和最小权限声明位于 `src-tauri/`。

```bash
pnpm tauri:dev          # 启动桌面开发版
pnpm tauri:build        # 构建正式桌面包（不包含测试驱动）
pnpm tauri:test:rust    # Rust Command 与原生逻辑测试
pnpm test:tauri         # 构建测试专用 App 并执行真实桌面 E2E
```

`test:tauri` 显式启用 `desktop-test` Rust Feature；WebdriverIO 插件和权限不会进入
普通 `tauri:dev` / `tauri:build` 产物。macOS 与 Windows 的构建和桌面冒烟由
`.github/workflows/tauri-desktop.yml` 共同验证。

开发模式下 `/api` 请求由 Vite 转发到 `http://127.0.0.1:8000`。

Demo Seed 的正式浏览器验收使用隔离测试数据库，测试结束后会删除该数据库，
不会读取或修改本机开发库：

```bash
pnpm e2e:demo-seed
```
