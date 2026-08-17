"""W9: ListingTemplateOut 响应模型补 store_overrides 字段测试。

验收：Out 模型能携带 store_overrides 序列化/反序列化，默认 None。
锁定「响应不再 drop store_overrides」——前端/skill 可读到多店铺差异化配置。
纯模型测试，无需 PG。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from api.schemas import ListingTemplateConfig, ListingTemplateOut


def test_out_carries_store_overrides():
    out = ListingTemplateOut(
        id="tpl-1",
        tenant_id="tenant-a",
        name="店铺差异化",
        store_overrides={"store-a": ListingTemplateConfig(margin_rate=0.3)},
    )
    dumped = out.model_dump()
    assert dumped["store_overrides"]["store-a"]["margin_rate"] == 0.3


def test_out_store_overrides_default_none():
    out = ListingTemplateOut(id="tpl-1", tenant_id="tenant-a", name="无覆盖")
    assert out.store_overrides is None
    assert "store_overrides" in out.model_dump()
    assert out.model_dump()["store_overrides"] is None


def test_out_roundtrip_json():
    out = ListingTemplateOut(
        id="tpl-1",
        tenant_id="tenant-a",
        name="店铺差异化",
        store_overrides={"store-a": ListingTemplateConfig(commission_rate=0.2, stock=50)},
    )
    parsed = ListingTemplateOut.model_validate_json(out.model_dump_json())
    assert parsed.store_overrides["store-a"].commission_rate == 0.2
    assert parsed.store_overrides["store-a"].stock == 50
