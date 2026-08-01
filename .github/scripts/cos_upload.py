#!/usr/bin/env python3
"""上传 Skill 包 + manifest 到腾讯云 COS（自动更新分发渠道）。

用法（CI）：python .github/scripts/cos_upload.py
环境变量：COS_SECRET_ID / COS_SECRET_KEY / COS_BUCKET / COS_REGION / PACKAGE_NAME

上传路径：
  /ozon-skill/<包名>.tar.gz
  /ozon-skill/manifest.json（覆盖，始终指向最新版本）
"""
import os
import sys


def main() -> int:
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        print("❌ 缺少 cos-python-sdk-v5，执行: pip install cos-python-sdk-v5")
        return 1

    # 全球加速域名：COS 控制台开通后设 SKILL 环境变量/COS_USE_ACCELERATE=1
    # （GitHub 美国 runner 直连广州 COS 会 UserNetworkTooSlow）
    use_accel = os.environ.get("COS_USE_ACCELERATE", "").lower() in ("1", "true", "yes")
    if use_accel:
        config = CosConfig(
            Region=os.environ["COS_REGION"],
            SecretId=os.environ["COS_SECRET_ID"],
            SecretKey=os.environ["COS_SECRET_KEY"],
            Endpoint="cos.accelerate.myqcloud.com",
        )
        print("🌐 使用全球加速域名上传")
    else:
        config = CosConfig(
            Region=os.environ["COS_REGION"],
            SecretId=os.environ["COS_SECRET_ID"],
            SecretKey=os.environ["COS_SECRET_KEY"],
        )
    client = CosS3Client(config)
    bucket = os.environ["COS_BUCKET"]

    # 1. 上传安装包 → /ozon-skill/<包名>
    # ⚠️ 包在 skill/ 下生成（Create distributable package 的 working-directory），
    # 本脚本在仓库根运行，需拼 skill/ 前缀
    # ⚠️ 用 upload_file 分片上传：GitHub runner 在美国跨太平洋传广州 COS，
    # 单请求 put_object 太慢会被拒（UserNetworkTooSlow），分片+多线程可解决
    pkg = os.path.join("skill", os.environ["PACKAGE_NAME"])
    if not os.path.exists(pkg):
        print(f"❌ 安装包不存在: {pkg}")
        return 1
    client.upload_file(
        Bucket=bucket,
        Key=f"ozon-skill/{os.environ['PACKAGE_NAME']}",
        LocalFilePath=pkg,
        MAXThread=10,          # 多线程并发加速跨洋上传
        PartSize=5 * 1024 * 1024,  # 5MB 分片
    )
    print(f"✅ 包已上传: /ozon-skill/{os.environ['PACKAGE_NAME']}")

    # 2. 上传 manifest（覆盖，始终指向最新版本）
    manifest_path = "skill/manifest.json"
    if not os.path.exists(manifest_path):
        print(f"❌ manifest 不存在: {manifest_path}")
        return 1
    client.put_object(Bucket=bucket, Body=open(manifest_path, "rb"),
                      Key="ozon-skill/manifest.json")
    print("✅ manifest 已上传: /ozon-skill/manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
