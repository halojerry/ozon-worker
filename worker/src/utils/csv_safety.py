"""CSV 公式注入中和（v0.76 T22，OWASP CSV Injection 口径）。
导出侧统一入口：凡用户可控文本（标题/供应商/notes/竞品词）写单元格前必过。"""
_CSV_DANGEROUS_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def neutralize_csv_cell(value):
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(_CSV_DANGEROUS_PREFIX):
        return "'" + value
    return value
