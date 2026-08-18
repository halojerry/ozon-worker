# Boot

Pounding 电商 vault（本地 Markdown 工作台）。

启动默认最多读取：

1. `AGENTS.md`
2. `DASHBOARD.md`
3. `00-System/Boot.md`（本文件）

进入本 vault 时，默认轻量读取索引：

1. `00-System/Active-Context.md`（当前状态）
2. `00-System/Memory-Index.md`（知识地图）

不需要用户显式触发。后续普通回答不强制重读；命中相关主题时按需读取对应内容文件。

配置类数据由 skill 命令自动落盘；结果类由 agent 判断落盘。默认 Quiet Mode（不展示后台过程）。
