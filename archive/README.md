# archive/ — 归档区

> 过时产物、历史截图、旧版文档的统一存放区。**仓库主体只保留当前有效内容**，
> 归档内容 Git 历史完整保留（`git mv` 移动，非删除），可随时恢复。

## 目录结构

| 子目录 | 内容 | 是否入库 |
|---|---|---|
| `screenshots/` | 开发过程截图 / 竞品调研截图 / 平台截图（png） | 否（本地保留，Git 未跟踪） |
| `packages/` | 历史压缩包（源码归档 zip、旧部署包 tar.gz） | 否（本地保留，Git 未跟踪） |
| `docs/legacy/` | 过时文档（旧版 PRD、早期助手简介、旧 CONTRACT） | 是（`git mv` 移动，历史可查） |

## 归档来源与恢复

- **截图/压缩包**：直接 `mv` 移入（从未入库，无 Git 历史）。需要时从 `archive/screenshots/` / `archive/packages/` 取回。
- **文档**：`git mv docs/<旧文档> archive/docs/legacy/`。需要时 `git mv archive/docs/legacy/<文件> docs/` 恢复。

## 归档判定标准

判断文件是否应归档（满足任一）：

1. **版本被取代**：同主题已有更新版本（如 `CONTRACT.md` 被 `CONTRACT-v4.md` 取代）
2. **过时**：内容引用旧版本号/旧架构，与当前 VERSION 严重脱节（如 `README_EN.md` 停在 v0.29.2）
3. **历史记录**：已完成的历史修复 PRD（如 `PRD-worker-stability-v2~v8`，v9 是最终版）
4. **一次性产物**：开发调试截图、竞品截图、临时打包文件

## 当前已归档清单

### docs/legacy/（已入库，git mv 移动）

- 早期助手简介：`POUNDING-Ozon-Assistant.md` / `_EN` / `_RU`
- 旧版契约：`CONTRACT.md`（v3.0，被 `docs/CONTRACT-v4.md` 取代）
- 历史 PRD：`PRD-worker-stability-v2~v8`（v9 为最终版）、`PRD-v2.md`、`PRD-v3.md`、`PRD-v0.21/0.26/0.27`、`PRD-discover-v2.md`、`PRD-image-generation-v2.md`

### screenshots/ 与 packages/（本地保留，未入库）

- 开发截图：`dashboard-*.png`、`login-*.png`、`final*.png`、`local-*.png` 等
- 竞品截图：`maozier-*.png`、`shopbang-*.png`
- 平台截图：`mxou-backend.png`、`mxou-homepage.png`
- 压缩包：`上品帮-源码归档.zip`、`ozon-worker-deploy.tar.gz`

---

*归档操作原则：宁可多归档也不要删除（Git 历史/本地文件双保险）；入库文档一律 `git mv` 保留历史。*