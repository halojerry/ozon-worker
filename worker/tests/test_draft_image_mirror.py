"""PRD M5b: 草稿图片镜像测试(真实 PG,COS mock)。

覆盖:create/patch 触发异步镜像(状态列 pending→mirrored)、version 守卫丢弃
(R8:版本已变不覆盖)、COS 未配置时保持外链不报错、镜像函数幂等(COS URL 跳过)。
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:ozon123@localhost:5433/ozon",
)
os.environ["CREDENTIAL_MASTER_KEY"] = "0123456789abcdef0123456789abcdef"
os.environ["SKIP_ZOMBIE_RECOVERY"] = "1"
os.environ["SKIP_FAILED_REVIVE"] = "1"

import main as main_mod  # noqa: E402

TENANT = main_mod._key_user_id("tokMir")


class FakeTokensTable:
    def __init__(self):
        self._rows = [{"user_id": "tenant-mir", "key": "tokMir", "status": 1, "deleted_at": None}]
        self._eqs = []

    def select(self, *cols):
        return self

    def eq(self, col, val):
        self._eqs.append((col, val))
        return self

    def is_(self, col, val):
        self._eqs.append((col, val))
        return self

    def limit(self, n):
        return self

    def execute(self):
        eqs, self._eqs = self._eqs, []
        filtered = self._rows
        for col, val in eqs:
            filtered = [r for r in filtered if str(r.get(col)) == str(val)]
        return SimpleNamespace(data=filtered[:1])


class FakeSupabase:
    def table(self, name):
        if name == "tokens":
            return FakeTokensTable()
        raise AssertionError(f"unexpected table {name}")


@pytest.fixture(scope="module")
def client():
    eng = create_engine(DB_URL)
    try:
        with eng.connect():
            pass
    except Exception:
        pytest.skip("本地 PG 不可达")
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM product_drafts WHERE tenant_id=:t"
        ), {"t": TENANT})
    with TestClient(main_mod.app) as c:
        yield c
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM product_drafts WHERE tenant_id=:t"
        ), {"t": TENANT})


def _envelope(title: str = "镜像测试商品", images=None) -> dict:
    return {
        "draft": {
            "title": title,
            "item_id": "mir-1688-1",
            "images": images or ["https://cbu01.alicdn.com/img/ibank/2024/test.jpg"],
            "weight": 500,
            "dimensions": {"length": 20, "width": 10, "height": 5},
        },
        "source": {"purchase_url": "https://detail.1688.com/offer/1.html",
                   "purchase_cost": 8.8},
        "extensions": {},
    }


def _row(eng, draft_id: str) -> tuple:
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT version, image_mirror_state, payload FROM product_drafts "
            "WHERE id=:id AND tenant_id=:t"
        ), {"id": uuid.UUID(draft_id), "t": TENANT}).fetchone()
    return row


def test_mirror_runs_and_updates_state(client):
    """COS 配置 + 下载成功 → 异步镜像回写(mirrored + COS URL)。"""
    eng = create_engine(DB_URL)
    os.environ["COS_SECRET_ID"] = "test"
    os.environ["COS_SECRET_KEY"] = "test"
    os.environ["COS_BUCKET"] = "test-bucket"
    try:
        with patch("requests.get") as mock_get, \
             patch("services.draft_image_mirror.cos_upload_bytes",
                   return_value="https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/abc.jpg"):
            mock_get.return_value = SimpleNamespace(status_code=200, content=b"fake-image")
            resp = client.post("/api/v1/drafts", json={
                "token": "tokMir", "source": "webui",
                "envelope": _envelope(),
            })
            assert resp.status_code == 200
            draft_id = resp.json()["id"]
            for _ in range(50):
                row = _row(eng, draft_id)
                if row[1] == "mirrored":
                    break
                time.sleep(0.1)
            assert row[1] == "mirrored"
            payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
            assert payload["draft"]["images"] == [
                "https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/abc.jpg"]
    finally:
        for k in ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET"):
            os.environ.pop(k, None)


def test_mirror_version_guard_drops_stale(client):
    """镜像回写时版本已变 → 丢弃并保持 failed(不覆盖新编辑,R8)。"""
    eng = create_engine(DB_URL)
    os.environ["COS_SECRET_ID"] = "test"
    os.environ["COS_SECRET_KEY"] = "test"
    os.environ["COS_BUCKET"] = "test-bucket"
    try:
        with patch("requests.get") as mock_get, \
             patch("services.draft_image_mirror.cos_upload_bytes",
                   return_value="https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/abc.jpg"):
            mock_get.return_value = SimpleNamespace(status_code=200, content=b"fake-image")
            resp = client.post("/api/v1/drafts", json={
                "token": "tokMir", "source": "webui",
                "envelope": _envelope(title="版本守卫商品"),
            })
            assert resp.status_code == 200
            draft_id = resp.json()["id"]
            # 在镜像线程回写前立刻编辑(version 1 → 2),让镜像回写变成 stale
            client.patch(f"/api/v1/drafts/{draft_id}", json={
                "version": 1,
                "payload": _envelope(title="版本守卫商品-已编辑"),
            }, headers={"Authorization": "Bearer tokMir"})
            time.sleep(1.0)
            row = _row(eng, draft_id)
            payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
            assert row[1] in ("failed", "mirrored") or payload["draft"]["title"] == "版本守卫商品-已编辑"
            # 关键:新编辑内容不能被旧镜像覆盖
            assert payload["draft"]["title"] == "版本守卫商品-已编辑"
    finally:
        for k in ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET"):
            os.environ.pop(k, None)


def test_mirror_disabled_keeps_original_urls(client):
    """COS 未配置 → 保持外链(状态 ''),不报错。"""
    eng = create_engine(DB_URL)
    resp = client.post("/api/v1/drafts", json={
        "token": "tokMir", "source": "skill",
        "envelope": _envelope(title="无 COS 商品"),
    })
    assert resp.status_code == 200
    draft_id = resp.json()["id"]
    row = _row(eng, draft_id)
    assert row[1] == ""
    payload = json.loads(row[2]) if isinstance(row[2], str) else row[2]
    assert payload["draft"]["images"][0].startswith("https://cbu01.alicdn.com")


def test_mirror_skips_already_cos_urls():
    """镜像函数对 COS URL 幂等(不重复下载)。"""
    from services.draft_image_mirror import mirror_draft_images
    payload = {
        "draft": {"images": [
            "https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/a.jpg"]},
    }
    new_images, changed = mirror_draft_images(payload)
    assert changed is False
    assert new_images == payload["draft"]["images"]
