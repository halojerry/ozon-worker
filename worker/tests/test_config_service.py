"""C1: 配置文件管理服务单测（纯文件系统，无需 PG/DB）。

验收（v0.55 系统设置 C1）：
1. list_configs 只返回 config 目录下的 *.json 文件名（排除 backup 子目录 / 非 json）
2. read_config 解析合法 JSON
3. write_config 收到非法 JSON → ValueError，文件不变，不产生备份
4. write_config 合法 → 文件更新 + config/backup/ 生成时间戳备份
5. 备份裁剪：保留最新 5 份（连续写 7 次 → 仅剩 5 个备份）
6. rollback_config 恢复上一版本内容
7. 路径穿越防护：../../etc/passwd / 无 .json 后缀 → ValueError
8. list_backups 按名称倒序返回 {name, size, mtime}
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import config_service


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 config 目录：2 个样例 JSON + 1 个非 json 文件，monkeypatch CONFIG_DIR。"""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "image_prompts.json").write_text(json.dumps({"version": 1, "theme": "warm"}), encoding="utf-8")
    (cfg / "translate_russian_cfg.json").write_text(json.dumps({"model": "deepseek-v4-flash-vision-exp"}), encoding="utf-8")
    (cfg / "README.txt").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(config_service, "CONFIG_DIR", cfg)
    return cfg


def _write_all(config_dir: Path, name: str, content: str) -> None:
    """绕过服务层直接写文件（构造备份数据用）。"""
    (config_dir / name).write_text(content, encoding="utf-8")


def test_list_configs_returns_json_names_only(config_dir: Path):
    """Given 目录含 2 个 json + 1 个 txt；When list_configs；Then 只返回 json 名。"""
    result = config_service.list_configs()

    names = {item["name"] for item in result}
    assert names == {"image_prompts.json", "translate_russian_cfg.json"}
    assert all(item["name"].endswith(".json") for item in result)


def test_list_configs_excludes_backup_dir(config_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Given backup 子目录含文件；When list_configs；Then backup 文件不出现。"""
    backup_dir = config_dir / "backup"
    backup_dir.mkdir()
    (backup_dir / "image_prompts.json.20260817120000.json").write_text("{}", encoding="utf-8")

    names = {item["name"] for item in config_service.list_configs()}

    assert names == {"image_prompts.json", "translate_russian_cfg.json"}


def test_read_config_parses_valid_json(config_dir: Path):
    """Given 合法 JSON 文件；When read_config；Then 返回解析后的 dict。"""
    result = config_service.read_config("image_prompts.json")

    assert result == {"version": 1, "theme": "warm"}


def test_write_config_invalid_json_no_change_no_backup(config_dir: Path):
    """Given 非法 JSON 内容；When write_config；Then ValueError，文件不变且无备份。"""
    with pytest.raises(ValueError):
        config_service.write_config("image_prompts.json", "{not valid json")

    assert json.loads((config_dir / "image_prompts.json").read_text(encoding="utf-8")) == {
        "version": 1,
        "theme": "warm",
    }
    assert not (config_dir / "backup").exists()


def test_write_config_valid_updates_and_backs_up(config_dir: Path):
    """Given 合法 JSON 内容；When write_config；Then 文件更新 + backup 目录生成备份。"""
    result = config_service.write_config("image_prompts.json", json.dumps({"version": 2, "theme": "cool"}))

    assert result["updated"] is True
    assert json.loads((config_dir / "image_prompts.json").read_text(encoding="utf-8")) == {
        "version": 2,
        "theme": "cool",
    }
    backups = list((config_dir / "backup").glob("*.json"))
    assert len(backups) == 1
    assert backups[0].name.startswith("image_prompts.json.")
    assert result["backup_path"] == str(backups[0])
    # 备份内容 = 旧版本
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"version": 1, "theme": "warm"}


def test_write_config_prunes_to_latest_5(config_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Given 连续写 7 次（时间戳递增）；When write_config；Then 备份仅保留最新 5 份。"""
    counter = {"n": 0}

    def fake_timestamp() -> str:
        counter["n"] += 1
        return f"20260817{120000 + counter['n']:06d}"

    monkeypatch.setattr(config_service, "_timestamp", fake_timestamp)

    for i in range(7):
        config_service.write_config("image_prompts.json", json.dumps({"version": i}))

    backups = sorted(p.name for p in (config_dir / "backup").glob("*.json"))
    assert len(backups) == 5
    # 保留的是最近 5 次写入前的内容（版本 1..5；write0 备份的是 fixture 初始内容）
    for backup_name, expected_version in zip(backups, range(1, 6)):
        content = json.loads((config_dir / "backup" / backup_name).read_text(encoding="utf-8"))
        assert content["version"] == expected_version


def test_rollback_config_restores_previous_content(config_dir: Path):
    """Given 先写 B（备份旧内容 A）；When rollback；Then 主文件恢复为 A。"""
    _write_all(config_dir, "translate_russian_cfg.json", json.dumps({"model": "v1"}))
    config_service.write_config("translate_russian_cfg.json", json.dumps({"model": "v2"}))
    backup_name = next((config_dir / "backup").glob("*.json")).name

    result = config_service.rollback_config("translate_russian_cfg.json", backup_name)

    assert result["name"] == "translate_russian_cfg.json"
    assert result["restored"] is True
    assert json.loads((config_dir / "translate_russian_cfg.json").read_text(encoding="utf-8")) == {
        "model": "v1"
    }


def test_path_traversal_rejected(config_dir: Path):
    """Given 穿越路径/无 .json 后缀；When 解析/读取；Then ValueError。"""
    with pytest.raises(ValueError):
        config_service._resolve_path("../../etc/passwd")
    with pytest.raises(ValueError):
        config_service._resolve_path("foo")
    with pytest.raises(ValueError):
        config_service.read_config("../../etc/passwd")
    # 合法名正常
    assert config_service._resolve_path("image_prompts.json") == config_dir / "image_prompts.json"


def test_list_backups_sorted_desc_with_size_mtime(config_dir: Path):
    """Given 3 个备份文件；When list_backups；Then 按名称倒序 + 含 size/mtime 字段。"""
    backup_dir = config_dir / "backup"
    backup_dir.mkdir()
    for ts in ("20260817100000", "20260817110000", "20260817120000"):
        _write_all(backup_dir, f"image_prompts.json.{ts}.json", json.dumps({"version": 1}))

    result = config_service.list_backups("image_prompts.json")

    assert [item["name"] for item in result] == [
        "image_prompts.json.20260817120000.json",
        "image_prompts.json.20260817110000.json",
        "image_prompts.json.20260817100000.json",
    ]
    for item in result:
        assert item["size"] > 0
        assert isinstance(item["mtime"], float)
