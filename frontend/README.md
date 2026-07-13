# Agent Platform Frontend

平台前端使用 React、TypeScript、Vite、Ant Design 和 Ant Design X。Web 与 Tauri 共用同一套业务页面，通过 `PlatformAdapter` 隔离平台能力。

```bash
pnpm install
pnpm dev
pnpm test
pnpm build
```

开发模式下 `/api` 请求由 Vite 转发到 `http://127.0.0.1:8000`。

Demo Seed 的正式浏览器验收使用隔离测试数据库，测试结束后会删除该数据库，
不会读取或修改本机开发库：

```bash
pnpm e2e:demo-seed
```
