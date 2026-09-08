# Ozon AI 自动化运营 ERP · API 对接文档

> 本文件不再维护端点清单。集成方阅读顺序：
> ① `docs/API-OVERVIEW.md`（对外约定：Base URL/鉴权/限流/错误码/分页/版本）
> ② `docs/API-REFERENCE.md`（全端点参考，自动生成）
> ③ Swagger `GET /docs`、`webui/src/imports/generated.d.ts`（TS 类型）
> ④ MCP 面见 `docs/MCP-SERVER.md`

本文档旧版（v0.56.6 手写端点清单/鉴权/模型/错误码正文）已由上述文档接管，为避免与代码漂移不再在此重复维护——端点以 `worker/src`（`main.py` 挂载 + `routes/*_routes.py`）为唯一真相源。
