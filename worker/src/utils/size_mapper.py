"""服装尺码映射工具 - 将中国/国际尺码映射到俄罗斯尺码"""
import os
import csv
import logging
from typing import Any, Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)

# 尺码表缓存
_size_tables: Dict[str, List[Dict[str, str]]] = {}
_loaded: bool = False

# 服装类目关键词（用于判断产品是否需要尺码）
CLOTHING_KEYWORDS = [
    "одежда", "куртка", "рубашка", "брюки", "штаны", "платье",
    "футболка", "свитер", "пиджак", "жилет", "комбинезон",
    "обувь", "ботинки", "сапоги", "кроссовки", "туфли",
    "перчатки", "варежки", "носки", "колготки",
    "шапка", "кепка", "шляпа", "балаклава",
    "каска", "шлем",  # 安全帽类
]

# 需要尺码属性的Ozon类目属性名
SIZE_ATTR_NAMES = ["Российский размер", "Размер", "Размер РФ"]


def _load_size_tables() -> None:
    """加载所有尺码表CSV文件"""
    global _loaded
    if _loaded:
        return

    assets_dir: str = os.path.join(os.getenv("APP_WORKSPACE_PATH", "/workspace/projects"), "assets")
    table_files: Dict[str, str] = {
        "children": os.path.join(assets_dir, "儿童服装尺码表.csv"),
        "male": os.path.join(assets_dir, "男性服装尺码表.csv"),
        "female": os.path.join(assets_dir, "女性服装尺码表.csv"),
        "shoes": os.path.join(assets_dir, "鞋子尺码对应表.csv"),
    }

    for table_name, file_path in table_files.items():
        if not os.path.exists(file_path):
            logger.warning(f"尺码表文件不存在: {file_path}")
            continue
        try:
            rows: List[Dict[str, str]] = []
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # 清理空值
                    clean_row: Dict[str, str] = {k.strip(): v.strip() for k, v in row.items() if k and v}
                    if clean_row:
                        rows.append(clean_row)
            _size_tables[table_name] = rows
            logger.info(f"加载尺码表 {table_name}: {len(rows)}行")
        except Exception as e:
            logger.error(f"加载尺码表 {file_path} 失败: {e}")

    _loaded = True


def is_clothing_product(product_name_ru: str, category_name: str = "") -> bool:
    """判断产品是否为服装类（需要尺码）"""
    _load_size_tables()
    combined: str = (product_name_ru + " " + category_name).lower()
    for kw in CLOTHING_KEYWORDS:
        if kw in combined:
            return True
    return False


def _normalize_size_input(size_str: str) -> str:
    """标准化尺码输入（去空格、大写）"""
    return size_str.strip().upper().replace(" ", "")


def map_size_to_russian(size_input: str, product_name_ru: str = "") -> Optional[Tuple[str, str]]:
    """
    将中国/国际尺码映射到俄罗斯尺码
    
    Args:
        size_input: 1688规格中的尺码值，如 "S", "M", "L", "XL", "38", "39"等
        product_name_ru: 产品俄语名称（用于判断服装类型）
    
    Returns:
        (russian_size, size_type) 如 ("46", "male") 或 None
        russian_size: 俄罗斯尺码字符串
        size_type: 使用的尺码表类型（male/female/children/shoes）
    """
    if not size_input or not size_input.strip():
        return None

    _load_size_tables()
    normalized: str = _normalize_size_input(size_input)

    # 判断产品类型
    name_lower: str = product_name_ru.lower()
    is_shoes: bool = "обув" in name_lower or "ботин" in name_lower or "сапог" in name_lower or "кроссов" in name_lower
    is_children: bool = "детск" in name_lower or "ребен" in name_lower or "дети" in name_lower
    is_female: bool = "женск" in name_lower or "дамск" in name_lower or "платье" in name_lower

    # 选择尺码表优先级
    if is_shoes:
        table_order: List[str] = ["shoes", "male", "female", "children"]
    elif is_children:
        table_order = ["children", "male", "female", "shoes"]
    elif is_female:
        table_order = ["female", "male", "children", "shoes"]
    else:
        table_order = ["male", "female", "children", "shoes"]

    for table_name in table_order:
        table: List[Dict[str, str]] = _size_tables.get(table_name, [])
        if not table:
            continue

        result: Optional[str] = _search_in_table(table, normalized, table_name)
        if result:
            logger.info(f"尺码映射成功: '{size_input}' -> '{result}' (table={table_name})")
            return (result, table_name)

    logger.warning(f"尺码映射失败: '{size_input}' 在所有尺码表中均未找到匹配")
    return None


def _search_in_table(table: List[Dict[str, str]], normalized_size: str, table_name: str) -> Optional[str]:
    """在尺码表中搜索匹配的俄罗斯尺码"""
    for row in table:
        # 遍历所有列，查找匹配的输入尺码
        for col_name, col_value in row.items():
            col_val_normalized: str = _normalize_size_input(col_value)
            if col_val_normalized == normalized_size:
                # 找到匹配，返回俄罗斯尺码列的值
                # 俄罗斯尺码列名可能是 "俄罗斯尺码"、"RU"、"Российский размер" 等
                ru_size: Optional[str] = None
                for ru_col in ["俄罗斯尺码", "RU", "Российскийразмер", "俄罗斯", "俄码"]:
                    if row.get(ru_col):
                        ru_size = row[ru_col].strip()
                        break
                if not ru_size:
                    # 如果没有明确的俄罗斯尺码列，尝试取第一列作为尺码
                    first_col: str = next(iter(row.values()), "")
                    ru_size = first_col.strip() if first_col else None

                if ru_size:
                    return ru_size

    return None


def get_size_variants_from_spec(spec_str: str) -> List[str]:
    """
    从1688规格字符串中提取尺码变体列表
    
    Args:
        spec_str: 如 "S、M、L、XL、XXL" 或 "38、39、40、41"
    
    Returns:
        尺码列表: ["S", "M", "L", "XL", "XXL"]
    """
    if not spec_str:
        return []

    # 中文顿号、逗号、空格分隔
    import re
    parts = re.split(r'[、,，\s/]+', spec_str.strip())
    sizes: List[str] = []
    for p in parts:
        p = p.strip()
        if p and len(p) <= 10:  # 尺码值不会太长
            # 排除明显不是尺码的值（如颜色名）
            if not any(color in p.lower() for color in ["红", "蓝", "绿", "黑", "白", "黄", "色"]):
                sizes.append(p)
    return sizes


def build_attribute_matching_table(
    attributes_schema: List[Dict],
    final_attributes: List[Dict],
    dictionary_values: Dict,
    draft_attrs: Dict
) -> str:
    """
    生成属性匹配对照表（用于日志和审计）
    
    格式：
    属性ID | 属性名 | 是否必填 | 1688对应值 | 匹配的字典值 | dictionary_value_id | 值来源
    
    Returns:
        格式化的对照表字符串
    """
    lines: List[str] = []
    lines.append("=" * 120)
    lines.append("📋 属性匹配对照表")
    lines.append("=" * 120)
    header: str = f"{'属性ID':<10} {'属性名':<25} {'必填':<6} {'1688值':<20} {'匹配字典值':<25} {'dict_value_id':<15} {'来源':<10}"
    lines.append(header)
    lines.append("-" * 120)

    # 构建final_attributes的索引
    final_map: Dict[int, Dict] = {}
    for attr in final_attributes:
        attr_id = attr.get("attribute_id")
        if attr_id is not None:
            final_map[int(attr_id)] = attr

    for schema_attr in attributes_schema:
        attr_id: int = int(schema_attr.get("id", 0))
        attr_name: str = str(schema_attr.get("name", ""))[:25]
        is_required: bool = schema_attr.get("is_required", False)
        required_str: str = "是" if is_required else "否"

        # 从1688数据中找对应值
        cn_value: str = ""
        if draft_attrs:
            for k, v in draft_attrs.items():
                if isinstance(v, str) and v and any(
                    kw in k for kw in [attr_name, schema_attr.get("name", ""), str(attr_id)]
                ):
                    cn_value = v[:20]
                    break

        # 从final_attributes中找匹配结果
        final_attr: Dict = final_map.get(attr_id, {})
        matched_value: str = str(final_attr.get("value", ""))[:25]
        dict_value_id: Any = final_attr.get("dictionary_value_id", "")
        source: str = final_attr.get("source", "-")

        if dict_value_id == -1 or dict_value_id == 0 or dict_value_id is None:
            dict_id_str: str = "⏭️ 跳过"
            matched_value = "无匹配值" if not matched_value else matched_value
        else:
            dict_id_str = str(dict_value_id)

        line: str = f"{attr_id:<10} {attr_name:<25} {required_str:<6} {cn_value:<20} {matched_value:<25} {dict_id_str:<15} {source:<10}"
        lines.append(line)

    lines.append("=" * 120)

    # 统计
    total: int = len(attributes_schema)
    matched: int = sum(1 for a in final_map.values() if a.get("dictionary_value_id", 0) and int(a.get("dictionary_value_id", 0)) > 0)
    skipped: int = sum(1 for a in final_map.values() if a.get("dictionary_value_id", -1) == -1 or a.get("dictionary_value_id", 0) == 0)
    required_missing: int = 0
    for schema_attr in attributes_schema:
        if schema_attr.get("is_required"):
            attr_id = int(schema_attr.get("id", 0))
            final_attr = final_map.get(attr_id, {})
            dict_id = final_attr.get("dictionary_value_id", 0)
            if not dict_id or int(dict_id) <= 0:
                # 检查是否是字典属性
                dict_id_in_schema = schema_attr.get("dictionary_id", 0)
                if dict_id_in_schema and int(dict_id_in_schema) > 0:
                    required_missing += 1

    lines.append(f"统计: 总计{total}个属性, 字典匹配成功{matched}个, 跳过/未匹配{skipped}个, 必填字典属性缺失{required_missing}个")
    if required_missing > 0:
        lines.append(f"⚠️ 警告: 有{required_missing}个必填字典属性未匹配到值！")

    table_str: str = "\n".join(lines)
    logger.info(table_str)
    return table_str


# 已知品牌名黑名单（用于过滤hashtags中的品牌名）
BRAND_BLACKLIST = {
    # 电商平台
    "amazon", "amazonbasics", "aliexpress", "ozon", "wildberries", "ebay",
    "速卖通", "亚马逊", "1688", "淘宝", "天猫", "京东",
    # 常见品牌
    "nike", "adidas", "puma", "underarmour", "apple", "samsung", "huawei",
    "xiaomi", "sony", "lg", "bosch", "makita", "dewalt", "blackdecker",
    "junglespoon", "lego", "disney", "marvel",
    # 中文营销词
    "跨境", "爆款", "现货", "创意", "外贸", "出口",
}


def filter_brand_from_hashtags(hashtags_str: str) -> str:
    """
    从hashtags字符串中过滤掉品牌名
    
    Args:
        hashtags_str: 如 "#garden #amazon #tools #nike"
    
    Returns:
        过滤后的hashtags: "#garden #tools"
    """
    if not hashtags_str:
        return hashtags_str

    import re
    tags: List[str] = re.findall(r'#(\w+)', hashtags_str)
    filtered_tags: List[str] = []
    removed: List[str] = []

    for tag in tags:
        tag_lower: str = tag.lower()
        is_brand: bool = False
        for brand in BRAND_BLACKLIST:
            if brand.lower() in tag_lower or tag_lower in brand.lower():
                is_brand = True
                removed.append(tag)
                break
        if not is_brand:
            filtered_tags.append(f"#{tag}")

    if removed:
        logger.info(f"hashtags品牌过滤: 移除 {removed}, 保留 {filtered_tags}")

    result: str = " ".join(filtered_tags)
    return result if result else hashtags_str
