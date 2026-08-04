"""生图节点模型配置单测（v0.25）— imagegen.json 热加载 + 回退。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from utils.image_models import get_image_model


def test_main_and_social_use_image2():
    assert get_image_model("main") == "gpt-image-2"
    assert get_image_model("social_proof") == "gpt-image-2"


def test_other_nodes_use_banana():
    for key in ("white_bg", "multi_angle", "scene_1", "scene_2", "scene_3",
                "comparison", "detail", "variant_white_bg"):
        assert get_image_model(key) == "nano-banana-fast", key


def test_unknown_key_falls_back():
    assert get_image_model("not_exist") == "gpt-image-2"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
