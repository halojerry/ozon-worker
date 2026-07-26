# Ozon Worker 问题追踪表

> 目标: 99% 上品成功率  
> 测试店铺: Client ID 5381204  
> 测试日期: 2026-07-26  
> 涉及组件: Skill (CDP抓取+信封组装) + Worker (全流程管线)

## 问题分类体系

| 分类 | 代码 | 说明 |
|------|------|------|
| Skill-CDP | S-CDP | Chrome CDP 抓取问题 (1688/Ozon 页面抓取) |
| Skill-Search | S-SRC | 1688 以图搜款/关键词搜索问题 |
| Skill-Envelope | S-ENV | 信封组装问题 (数据完整性、字段格式) |
| Worker-Category | W-CAT | 类目匹配问题 (description_category_id/type_id) |
| Worker-Attribute | W-ATTR | 属性填充问题 (字典值/自由文本) |
| Worker-Image | W-IMG | 图片生成问题 (MXOU 生图失败/质量) |
| Worker-Validate | W-VAL | Ozon 预检测问题 |
| Worker-Upload | W-UPL | Ozon 上传问题 (API 错误) |
| Worker-Quota | W-QTA | 配额问题 (日限额/总上限) |
| Worker-Pricing | W-PRC | 定价问题 (物流费率/利润计算) |
| Infra | INFRA | 基础设施问题 (Docker/网络/DB) |

---

## 实时问题记录

### 批次 1: Ozon 跟卖 (22 产品)

| # | 产品 ID | 问题分类 | 严重度 | 现象 | 根因 | 状态 |
|---|---------|----------|--------|------|------|------|
| - | - | - | - | 处理中... | - | - |

### 批次 2: 1688 直上 (71 产品)

| # | Offer ID | 问题分类 | 严重度 | 现象 | 根因 | 状态 |
|---|----------|----------|--------|------|------|------|
| - | - | - | - | 待处理 | - | - |

---

## 已知系统性问题 (历史)

### 1. [W-CAT-001] type_id 不匹配 → DESCRIPTION_DECLINE
- **严重度**: 🔴 High
- **现象**: Ozon 报 `DESCRIPTION_DECLINE`，type_id=0 或类目不匹配
- **根因**: pg_trgm 搜索找不到匹配的俄语 type 名称（类目树缺少部分 type 如 "Лейка садовая"）
- **状态**: 🔧 已部分修复 (pg_trgm + LLM 回退)，仍有 23 个产品 type 不匹配

### 2. [W-ATTR-001] 字典缓存多语言不一致
- **严重度**: 🔴 High
- **现象**: ZH_HANS 写入 RU 表，或反之，导致缓存命中但值错误
- **根因**: `_cache_dict_values()` 缺 `language` 参数
- **状态**: ✅ 已修复 (v0.6.0)，分离 ZH_HANS/RU 缓存表

### 3. [W-IMG-001] product_id 缺失 → retry loop 创建重复产品
- **严重度**: 🔴 High
- **现象**: retry loop 无 product_id → 创建无图片的重复产品 → Ozon 上两个产品
- **根因**: `ValidationRetryLoopInput` 缺少 `product_id` 字段
- **状态**: ✅ 已修复 (v0.6.0)，3 处代码添加 product_id 传递

### 4. [W-UPL-001] 价格 API 路径错误
- **严重度**: 🟡 Medium
- **现象**: `/v1/product/prices/update` 返回 404
- **根因**: Ozon API 正确路径是 `/v1/product/import/prices`
- **状态**: ✅ 已修复

### 5. [S-CDP-001] 1688 登录态丢失
- **严重度**: 🟡 Medium
- **现象**: CDP 抓取时提示需要重新登录 1688
- **根因**: Cookie 过期或浏览器会话超时
- **状态**: 🔧 需重新扫码登录（临时方案）

### 6. [W-IMG-002] 生图标题含平台名/营销词
- **严重度**: 🟡 Medium
- **现象**: AI 生图 prompt 含 "1688"、"抖音同款"、"跨境爆款"
- **根因**: 1688 标题直接嵌入 prompt
- **状态**: ✅ 已修复，`clean_title_for_image_prompt()` 过滤 80+ 垃圾词

### 7. [W-IMG-003] 跟卖走竞品原图而非 AI 生图
- **严重度**: 🟢 Low (已修复)
- **现象**: 跟卖产品使用竞品 Ozon 图片而非 AI 生成新图
- **根因**: `route_after_follow_sell_import` 直接跳 END
- **状态**: ✅ 已修复 (v0.6.0)，跟卖走完整生图管线

### 8. [W-QTA-001] 缺少配额预检查
- **严重度**: 🟡 Medium
- **现象**: 配额耗尽后仍提交任务，浪费 MXOU 生图额度
- **根因**: 无 submit_task 阶段配额检查
- **状态**: ✅ 已修复 (v0.6.1)，双阶段配额检查

### 9. [INFRA-001] Docker Desktop macOS I/O 错误
- **严重度**: 🟡 Medium
- **现象**: `docker build` 报 disk I/O error
- **根因**: Docker Desktop BuildKit metadata 损坏
- **状态**: 🔧 需 `docker system prune` 后重试

---

## 生产级待办

- [ ] [W-CAT-002] type_id pg_trgm 找不到时自动请求 Ozon `/v1/description-category/attribute` 获取所有 type
- [ ] [S-CDP-002] 1688 Cookie 自动续期机制（定期心跳保活）
- [ ] [W-IMG-004] MXOU 生图 fallback 策略（COS 未生成 → 重试 → 1688 原图）
- [ ] [W-QTA-002] 配额紧张时自动暂停入队并告警
- [ ] [W-VAL-002] WARNING 级别错误人工审核标记（不可自动修复的属性值范围错误）
- [ ] [INFRA-002] 日志持久化到 PG（当前重启丢失）
- [ ] [INFRA-003] 进度持久化（当前内存存储，重启丢失）
- [ ] Multi-agent 兼容性测试（openclaw/Hermes/Claude/codex/workerbuddy）
