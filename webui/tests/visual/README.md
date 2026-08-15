# 视觉回归基线（M2.4）

设计系统接入的「视觉不变」守门人：任何页面改动前后截图对比，像素级无差异才算过。

## 首选方案：Playwright（未安装，预留）

`tests/visual/smoke.spec.ts` 是 Playwright 冒烟基线（打开各页面 → 截图 → 存 `baseline/`），
仓库当前未安装 Playwright（webui 依赖面刻意保持最小）。启用步骤：

```bash
cd webui
npm i -D @playwright/test && npx playwright install chromium
npm run build && npm run preview -- --port 4173 &     # 必须跑生产构建
npx playwright test tests/visual/smoke.spec.ts --update-snapshots   # 首次生成基线
npx playwright test tests/visual/smoke.spec.ts                      # 后续回归
```

## 当前替代方案：Chrome CDP 冒烟（零依赖，已在用）

Playwright 不可用期间，用现有 Chrome CDP 冒烟模式（与 skill 的 CDP 方案同源）：
headless Chrome → 注入 localStorage token（绕过登录）→ 逐路由截图 → Pillow 像素 diff。

```bash
cd webui
npm run build && npm run preview -- --port 4173 &      # 生产构建 + 预览
PYTHON=/Volumes/os/dev/ozon-worker/skill/.venv314/bin/python   # 有 requests + websocket-client + Pillow

# 截图（desktop 1440x900 + mobile 390x844）
$PYTHON tests/visual/capture_cdp.py --out /tmp/qa-before/desktop \
    --base http://localhost:4173/app --routes login collect-box products stores tasks on-sale image-studio --mobile
# …改动代码后再次截图到 /tmp/qa-after/…
$PYTHON tests/visual/diff_images.py --before /tmp/qa-before/desktop --after /tmp/qa-after/desktop
```

退出码 0 = 无像素差异；diff_images 打印逐路由差异 ratio + bbox 定位。

## 截图确定性（两脚本共用约定）

1. **生产构建产物**（`vite preview`，与线上一致；dev server 不用于基线）。
2. **注入 token** `ozon_webui_token` → 保护路由渲染壳布局；无 token 全部重定向登录页。
3. **冻结动画**（注入 CSS `* { animation: none !important; transition: none !important; }`，
   DOMContentLoaded 后挂 head）——spinner 相位不漂移，前后截图可比。
   注入是测试态覆盖，不改应用源码。
4. 固定视口 desktop 1440x900 / mobile 390x844（覆盖 768px 窄屏断点行为）。
5. 页面 /api 请求打到本地 worker（vite preview 继承 server.proxy → localhost:8080），
   返回 401/空数据 → 稳定错误/空态，前后可比。

## 已知坑（本轮实测）

- `Page.addScriptToEvaluateOnNewDocument` 阶段 `document.documentElement` 为 **null**：
  直接 `appendChild` 抛错会吞掉同一脚本里后续的 `localStorage.setItem`（首轮截图全部
  登录页的根因）。注入顺序：setItem 在前，DOM 操作延迟到 DOMContentLoaded。
- 解析期向 documentElement 挂样式会导致 **body 解析中断**（readyState=complete 但
  document.body===null → 全空白图）。必须等 DOMContentLoaded 后挂 head。
- headless Chrome 启动有竞态：固定 sleep 可能截到未解析帧；capture_cdp.py 用
  readyState/body 轮询 + 超时重导航兜底。
- WebSocket 直连 CDP 需 `--remote-allow-origins=*`，否则 403。

## 基线目录约定

```
tests/visual/
├── smoke.spec.ts        # Playwright 基线（预留，未安装）
├── capture_cdp.py       # CDP 截图（当前生效）
├── diff_images.py       # Pillow 像素 diff
├── README.md            # 本文件
└── baseline/            # 基线截图（Playwright --update-snapshots 生成）
    ├── desktop/<route>.png
    └── mobile/<route>.png
```
