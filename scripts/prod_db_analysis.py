# -*- coding: utf-8 -*-
"""生产库只读分析脚本（供 ozon_ro 在服务器或公网 15433 执行）。

用法：
    python scripts/prod_db_analysis.py "postgresql://ozon_ro:***@host:15433/ozon"

纪律：SELECT-only + statement_timeout 30s + 逐查询容错；输出全为聚合计数，无 PII。
"""
import os
import sys

import psycopg2


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PGDATABASE_URL", "")
    if not url:
        print('usage: prod_db_analysis.py "postgresql://ozon_ro:***@host:15433/ozon"')
        sys.exit(2)
    conn = psycopg2.connect(url, connect_timeout=10)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SET statement_timeout = '30s'")

    print("\n== A1 任务终态分布(全量) ==")
    try:
        cur.execute("SELECT status, COUNT(*) FROM ozon_product_tasks GROUP BY status ORDER BY 2 DESC")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== A2 近14天任务按日终态 ==")
    try:
        cur.execute("SELECT created_at::date AS d, status, COUNT(*) FROM ozon_product_tasks WHERE created_at > NOW() - INTERVAL '14 days' GROUP BY 1, 2 ORDER BY 1 DESC, 2")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== A3 近14天failed错误Top15 ==")
    try:
        cur.execute("SELECT LEFT(error_message, 90) AS err, COUNT(*) FROM ozon_product_tasks WHERE status = 'failed' AND created_at > NOW() - INTERVAL '14 days' GROUP BY 1 ORDER BY 2 DESC LIMIT 15")
        for row in cur.fetchall():
            print(" | ".join(str(v)[:90] for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== A4 近14天failed错误码Top10 ==")
    try:
        cur.execute("SELECT result->>'error_code' AS code, COUNT(*) FROM ozon_product_tasks WHERE status = 'failed' AND created_at > NOW() - INTERVAL '14 days' AND result ? 'error_code' GROUP BY 1 ORDER BY 2 DESC LIMIT 10")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== B1 同步域错误逐店(近10) ==")
    try:
        cur.execute("SELECT tenant_id, credential_id, LEFT(orders_error, 60) AS o_err, LEFT(products_error, 60) AS p_err, orders_sync_incomplete, orders_last_synced_at FROM credential_sync_state ORDER BY updated_at DESC LIMIT 10")
        for row in cur.fetchall():
            print(" | ".join(str(v)[:70] for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== B2 同步域错误计数 ==")
    try:
        cur.execute("SELECT COUNT(*) FILTER (WHERE orders_error <> '') AS orders_err, COUNT(*) FILTER (WHERE products_error <> '') AS products_err, COUNT(*) AS total FROM credential_sync_state")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== B3 同步job近7天状态 ==")
    try:
        cur.execute("SELECT status, COUNT(*) FROM store_sync_jobs WHERE created_at > NOW() - INTERVAL '7 days' GROUP BY 1")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== B4 同步job近7天错误Top8 ==")
    try:
        cur.execute("SELECT LEFT(error, 90) AS err, COUNT(*) FROM store_sync_jobs WHERE created_at > NOW() - INTERVAL '7 days' AND error <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 8")
        for row in cur.fetchall():
            print(" | ".join(str(v)[:90] for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== C1 留存终态分布 ==")
    try:
        cur.execute("SELECT final_status, COUNT(*) FROM listing_result_log GROUP BY 1 ORDER BY 2 DESC")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== C2 留存failed且error_code空(取证断链残余) ==")
    try:
        cur.execute("SELECT COUNT(*) FROM listing_result_log WHERE final_status = 'failed' AND (error_code IS NULL OR error_code = '')")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== C3 留存error_code Top10 ==")
    try:
        cur.execute("SELECT error_code, COUNT(*) FROM listing_result_log WHERE error_code IS NOT NULL AND error_code <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 10")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== C4 留存pipeline×终态 ==")
    try:
        cur.execute("SELECT pipeline_source, final_status, COUNT(*) FROM listing_result_log GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== C5 留存近14天按日终态 ==")
    try:
        cur.execute("SELECT completed_at::date AS d, final_status, COUNT(*) FROM listing_result_log WHERE completed_at > NOW() - INTERVAL '14 days' GROUP BY 1, 2 ORDER BY 1 DESC, 2")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== D1 learning_record 源×净分 ==")
    try:
        cur.execute("SELECT source, success_count, fail_count, COUNT(*) FROM learning_record GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 10")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== D2 learned权威行数(succ>=2) ==")
    try:
        cur.execute("SELECT COUNT(*) FROM learning_record WHERE source = 'learned' AND success_count >= 2")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== E1 attribute_cache 覆盖 ==")
    try:
        cur.execute("SELECT COUNT(*) AS rows, MAX(updated_at) AS freshest FROM attribute_cache")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== E2 dictionary_value_cache 规模 ==")
    try:
        cur.execute("SELECT COUNT(*) AS rows, pg_size_pretty(pg_total_relation_size('dictionary_value_cache')) AS size FROM dictionary_value_cache")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== E3 warm_dead_nodes 失效节点 ==")
    try:
        cur.execute("SELECT COUNT(*) FROM warm_dead_nodes")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== E4 error_reports 全租户 ==")
    try:
        cur.execute("SELECT status, COUNT(*) FROM error_reports GROUP BY 1")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== F1 店铺指标快照 ==")
    try:
        cur.execute("SELECT COUNT(*), MAX(snapshot_at) FROM store_metrics_history")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    print("\n== F2 选品洞察规模 ==")
    try:
        cur.execute("SELECT COUNT(*) FROM selection_insights")
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
    except Exception as exc:
        print("  skip:", str(exc)[:150])

    conn.close()


if __name__ == "__main__":
    main()
