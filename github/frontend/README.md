# jt-ipam frontend

Vue 3 + TypeScript + Vite + Naive UI + Pinia + vue-i18n。

## 開發

```bash
pnpm install
pnpm dev          # http://localhost:5173
pnpm typecheck
pnpm lint
pnpm build
```

## 結構

```
src/
├── main.ts
├── App.vue            # n-config-provider + 主題 / locale 注入
├── router/            # vue-router 4
├── stores/ui.ts       # 主題、語言、使用者偏好
├── i18n/              # zh-TW / en-US
├── api/               # axios client + per-resource API 模組
├── views/             # 頁面（Dashboard、Sections、Subnets、Addresses…）
└── components/layout/ # 樹狀導航 + 頂部列
```

## 主題與語言

- 主題：light / dark / auto（auto 跟隨系統）
- 語言：zh-TW（一級）/ en-US（完整對應）
- 切換在右上角；偏好儲存於 localStorage（將同步到後端 `user_preferences`）
