"""v0.83.2: 类目文档硬要求闸（判定 + 学习，唯一读写入口）。

第一性原理（改前必读）：
- 管线对「类目硬性要求」的发现方式必须是**预检**而不是**试错**。每次试错 =
  白烧一次 Ozon import 配额 + 生图/LLM 额度 + 用户等 3 分钟看 failed。
- 2026-10-02 生产实锤（task da284d0e，release 0.83.1）：袜子类目 CREATE，
  v0.83.1 已整键省略 pdf_list（7e4d095d），卡建到 Ozon 后 validation 仍拒
  PDF_SRC_URL_IS_EMPTY（«Ссылка на pdf не может быть пустая»）——证明该类目
  的文档要求是平台侧硬要求，发 [] 与不发都过不了。这不是载荷形状问题，是
  「该类目我们供不出合规文档」的类目适配问题，载荷层补丁永远治不住。
- 本模块把「已经付过学费的事实」固化：decline 学习（PDF_SRC_URL_IS_EMPTY
  拒单自动 upsert）+ curated 种子（config 热加载）→ assemble 预检命中即入
  采集箱（阻断出口在 assemble_ozon_product_node._doc_required_exit），零白烧。

数据分层（读优先级；✅ v0.83.2 验收修复起 curated 恒赢 assemble 豁免阶梯）：
0. curated_doc_requirement —— config/requires_doc_categories.json（curated，
   人工维护，热加载——每次调用现读磁盘，改文件下一次调用生效，语义对齐
   utils/restricted_keywords.py）；支持 (dc, tp) 精确行与 (dc, 0) 类目级通配行。
   assemble 在豁免阶梯（_doc_gate_exempt）**之前**判定本层：人工确认的类目
   事实对 manual/page/what_to_sell/widget 可信来源照样硬（R1 veto 对人工来源
   不松动的同款哲学）——否则 discover 主流（what_to_sell/page 源）连同
   curated/学习表一起绕过，需文档类目每单白烧 + 运营人工登记形同虚设。
1. category_doc_requirements 学习表（decline 学习自动积累；对齐
   attr_bounds_learned 先例：全局共享无 tenant，evidence 留拒单原文供人工
   复核，人工确认后可晋升进 curated 配置）——**仅在豁免阶梯之内生效**
   （自动链路 L0/L1/R2b/search_kw 保护口径不变）。requires_document =
   curated + 学习表，是非豁免路径的合并判定入口。

红线：
- 本模块只做「判定 + 记录」，不做阻断动作（唯一入箱写侧仍是
  utils/blocked_draft_box.py）。
- requires_document 任何异常（表不存在/DB 抖动）→ 返回 None fail-open：
  学习库不可用不拦正常上架流（闸的职责是省配额，不是制造新阻断面）。
- record_doc_requirement 失败必须非致命（调用方 try/except + warning），
  学习写失败不挡任务终态落库。
- DB 访问走 SQLAlchemy ORM 表达式（编译为绑定参数，等价占位符参数化查询；
  与 main.py discovery upsert 同一形态）。
"""
import logging
import os

from storage.database.db import get_engine

logger = logging.getLogger(__name__)

# 文档型硬要求的 Ozon 拒单错误码集合（唯一触发学习的信号；新码在此追加）
DOC_REQUIREMENT_DECLINE_CODES = {"PDF_SRC_URL_IS_EMPTY"}

_NOTICE = ("该类目需商品合规文档（PDF），自动上架无法提供；已在采集箱待人工处理"
           "（可换类目，或为店铺配置合规文件后经采集箱人工提交）")


def _config_path() -> str:
    """curated 配置绝对路径（恒定在 workspace/config/ 下，含 containment 校验）。"""
    root = os.path.realpath(os.getenv("APP_WORKSPACE_PATH") or os.getcwd())
    cfg_path = os.path.realpath(os.path.join(root, "config", "requires_doc_categories.json"))
    if not (cfg_path == root or cfg_path.startswith(root + os.sep)):
        # 防御：路径必须落在 workspace 内（env 异常时不读盘，回退空 curated 层）
        logger.warning("类目文档要求配置路径越界(%s)，忽略 curated 层", cfg_path)
        return ""
    return cfg_path


def _load_curated() -> list[dict]:
    """现读 config/requires_doc_categories.json（每次调用读盘 → 热加载）。

    结构：{"categories": [{"description_category_id": int, "type_id": int,
    "note": str}, ...]}；type_id=0 表示类目级通配（该 dc 下所有 type）。
    文件缺失/损坏/格式错 → 空 list（curated 层失能，学习表照常生效），绝不抛异常。
    """
    import json

    cfg_path = _config_path()
    if not cfg_path:
        return []
    try:
        with open(cfg_path, "r", encoding="utf-8") as fd:
            data = json.load(fd)
        rows = data.get("categories") if isinstance(data, dict) else None
        if isinstance(rows, list):
            out: list[dict] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    dc = int(row.get("description_category_id") or 0)
                    tp = int(row.get("type_id") or 0)
                except (TypeError, ValueError):
                    continue
                if dc <= 0 or tp < 0:
                    continue
                out.append({
                    "description_category_id": dc,
                    "type_id": tp,
                    "note": str(row.get("note") or "")[:200],
                })
            return out
        logger.warning("类目文档要求配置格式错误(%s): 期望 categories 列表，忽略", cfg_path)
    except FileNotFoundError:
        logger.info("类目文档要求配置不存在(%s)，curated 层为空", cfg_path)
    except Exception as e:
        logger.warning("类目文档要求配置加载失败(%s): %s，忽略", cfg_path, e)
    return []


def _curated_hit(dc_i: int, tp_i: int) -> dict | None:
    """curated 层判定（纯函数）：精确 (dc,tp) > (dc,0) 类目级通配。"""
    for row in _load_curated():
        if row["description_category_id"] != dc_i:
            continue
        if row["type_id"] in (tp_i, 0):
            return {
                "source": "curated",
                "note": row["note"] or "人工登记：该类目需商品合规文档",
                "times_seen": 0,
            }
    return None


# ── 学习表 SQL（静态字符串 + %s 占位符 + 参数元组，经 exec_driver_sql 参数化
# 编译——值恒走 bind 通道，SQL 文本零拼接；JSONB 走 %s::jsonb 参数化 cast）──
_SQL_SELECT_LEARNED = (
    "SELECT source, evidence, times_seen "
    "FROM category_doc_requirements "
    "WHERE description_category_id = %s AND type_id = %s "
    "LIMIT 1"
)
_SQL_UPSERT_LEARNED = (
    "INSERT INTO category_doc_requirements "
    "  (description_category_id, type_id, source, evidence) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (description_category_id, type_id) DO UPDATE SET "
    "  times_seen = category_doc_requirements.times_seen + 1, "
    "  last_seen_at = NOW(), "
    "  evidence = EXCLUDED.evidence"
)


def requires_document(dc: int, tp: int) -> dict | None:
    """判定 (dc, tp) 是否为「需商品文档」类目（只读，无任何写副作用）。

    返回 {"source": "curated"/"decline_learned", "note": str, "times_seen": int}
    或 None（不要求/未知）。curated 恒赢（精确 (dc,tp) > (dc, 0) 通配）> 学习表；
    任何 DB 异常 → None（fail-open，见模块红线）。
    """
    try:
        dc_i, tp_i = int(dc or 0), int(tp or 0)
    except (TypeError, ValueError):
        return None
    if dc_i <= 0 or tp_i <= 0:
        return None

    hit = _curated_hit(dc_i, tp_i)
    if hit:
        return hit

    # 学习表（fail-open：表未建/DB 异常 = 未学习过，放行）
    try:
        with get_engine().connect() as conn:
            found = conn.exec_driver_sql(
                _SQL_SELECT_LEARNED, (dc_i, tp_i)).fetchone()
        if found:
            return {
                "source": str(found[0] or "decline_learned"),
                "note": "拒单学习：该类目曾被 Ozon 以文档缺失拒绝"
                        f"（times_seen={int(found[2] or 1)}）",
                "times_seen": int(found[2] or 1),
                "evidence": found[1] if isinstance(found[1], dict) else {},
            }
    except Exception as e:
        logger.warning("类目文档要求学习表查询失败 dc/tp=%s/%s（fail-open 放行）: %s",
                       dc_i, tp_i, e)
    return None


def curated_doc_requirement(dc: int, tp: int) -> dict | None:
    """curated 层单独判定（只读盘，零 DB、零写副作用；fail-open：异常 → None）。

    ✅ v0.83.2 验收修复（fix/v0832-review-findings-v1）：assemble 在豁免阶梯
    **之前**调用本函数——人工确认的 curated 类目事实对 manual/page/what_to_sell/
    widget 可信来源照样硬（R1 veto 对人工来源不松动的同款哲学）。豁免阶梯先于
    本层生效时，discover 主流（what_to_sell/page 源）会连同 curated 与学习表
    一起绕过：需文档类目每单白烧 import+生图，运营按升级指引人工登记 config
    也形同虚设。学习表（decline 自动积累）仍只在豁免阶梯之内生效。
    """
    try:
        dc_i, tp_i = int(dc or 0), int(tp or 0)
    except (TypeError, ValueError):
        return None
    if dc_i <= 0 or tp_i <= 0:
        return None
    try:
        return _curated_hit(dc_i, tp_i)
    except Exception as e:  # 读盘/解析异常 → curated 层失能放行（对齐 _load_curated 红线）
        logger.warning("curated 文档要求判定异常（fail-open 放行）: %s", e)
        return None


def record_doc_requirement(dc: int, tp: int, evidence: dict | None = None,
                           source: str = "decline_learned") -> bool:
    """拒单学习 upsert：(dc, tp) 命中文档缺失拒单 → 记录/累加。

    幂等语义：ON CONFLICT (dc, tp) → times_seen+1 + last_seen_at=now + evidence
    覆盖为最新（拒单原文以最近一次为准）。返回是否写入成功；**调用方必须
    try/except 包裹本函数并把失败视为非致命**（学习失败不挡终态落库）。
    """
    try:
        dc_i, tp_i = int(dc or 0), int(tp or 0)
    except (TypeError, ValueError):
        return False
    if dc_i <= 0 or tp_i <= 0:
        return False
    ev = evidence if isinstance(evidence, dict) else {}
    # cap：evidence 是人工复核面，防大拒单原文撑爆行（≤12 键、字符串值 ≤300 字）
    ev = {str(k)[:60]: (str(v)[:300] if isinstance(v, (str, int, float)) else v)
          for k, v in list(ev.items())[:12]}
    src = str(source or "decline_learned")[:32]
    try:
        import json as _json

        with get_engine().begin() as conn:
            conn.exec_driver_sql(
                _SQL_UPSERT_LEARNED,
                (dc_i, tp_i, src, _json.dumps(ev, ensure_ascii=False)),
            )
        logger.info("📥 类目文档要求学习写入: dc/tp=%s/%s source=%s", dc_i, tp_i, src)
        return True
    except Exception as e:
        logger.warning("类目文档要求学习写入失败 dc/tp=%s/%s（非致命）: %s", dc_i, tp_i, e)
        return False


def doc_gate_notice(info: dict | None = None) -> str:
    """闸命中时给用户的 notice 文案（与 restricted_notice 同位职责）。"""
    return _NOTICE
