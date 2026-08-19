# 浏览器策略（Browser Strategy）

> 结论（2026-08-20 实测验证）：**skill Chrome + Electron 双浏览器，是能过反爬 + 客户端可见的最优组合。**
> dsh 浏览器插件（ego-browser / dsh-browser 等）解决「agent 逛网页」，替代不了 skill 采集。

## 一、最终分工

| 浏览器 | 承担 | 依据（实测） |
|---|---|---|
| **skill Chrome**（自启，1688 profile）| **Ozon 深采集**（discover/follow/seller）+ 1688 采集兜底 | 完整 stealth + Ozon/1688 登录态 + 可信指纹，**能过 Ozon WAF** |
| **Electron 浏览器宿主**（我们维护，`pounding-harness/electron-browser`）| **1688 采集**（search/probe/image_search，客户端可见可接手）+ 客户端浏览器体验 | BrowserWindow + executeJavaScript（shopbang 同款），persist session 登录态复用 |
| dsh 浏览器插件（ego/dsh-browser）| agent 通用浏览（逛任意网页）| 不给 skill 提供可附加的浏览器，见 §三 |

## 二、为什么 skill Chrome 必须保留（Ozon 反爬门槛）

Ozon 对自动化浏览器的拦截是 **IP/指纹层**（实测）：
- Electron 打开 Ozon 首页/登录页 → 全部返回「Похоже, нет соединения」（WAF 拦，登录都做不了）
- skill Chrome 能过 = 真实 Chrome 151 + skill 完整 stealth + Ozon 登录态 + 长期可信 profile

**结论**：Ozon 深采集只能靠 skill Chrome；为了「统一浏览器」丢掉它是错误方向。

## 三、dsh 浏览器插件边界（源码级调研 2026-08-20）

| 插件 | 浏览器形态 | 能否被 skill(Python) 附加 |
|---|---|---|
| Lum1104/dsh-browser | Chrome 扩展 + token WebSocket bridge（`/ext/bridge` 专用协议）| ❌ 非 CDP，且日常 Chrome 无 CDP 端口 |
| ego-browser | ego-lite Chromium（`--remote-debugging-port=0` 随机端口，内部管理）| ❌ 随机端口，无法稳定附加 |
| wqty123/dsh-browser | Electron WebContentsView + 自定义 RPC | ❌ 非标准 CDP |
| dsh-builtin-browser | 内置浏览器（agent 专用）| ❌ |

**共同点**：都是「agent 操作网页」的工具，浏览器不暴露标准 CDP 端口给外部进程。可以装来增强 agent 通用浏览，但**不能让 skill 采集更轻**。

## 四、Electron 宿主（shopbang 式）能力

`pounding-harness/electron-browser`（92x 端口）：
- **CDP 9222**：skill `ensure_chrome_cdp(port=9222)` 附加（复用主窗口，1688 采集）
- **ops API 9224/ops/\***：独立 BrowserWindow 操作（open/exec/html/close），persist 登录态共享
- **skill 侧 `electron_ops.py`**：probe_1688 走 ops API（BrowserWindow + executeJavaScript，shopbang 同款）
- 网关（boujoy_server）启动拉起 + 60s 保活；Electron 异常 → skill 降级自启 Chrome

**登录态**：`persist:pounding` session 落盘——1688 扫码一次，之后 skill 采集自动复用。

## 五、维护要点

1. **Electron 必须 `env -u ELECTRON_RUN_AS_NODE` 启动**（否则退化成 Node）；网关 spawn 时已处理
2. **GPU 受限环境**：main.js 已 `disable-gpu` + `use-gl=swiftshader` 软件渲染
3. **UA 伪装**：`app.userAgentFallback` 设标准 Chrome UA（去 pounding/Electron 标记）
4. **主备降级**：Electron 可用 → skill 附加 Electron；不可用 → skill 自启 Chrome，采集永不中断
5. **Ozon 采集若未来要上客户端浏览器**：需给 Electron 做完整 stealth + 换可信 IP/代理（成本高，暂不做）
