"""预建 dsh web profile（确定性手写）。

bundle 树由各包 dsh.bundle.patch 合成（@deepseek-ai/dsh-base、dsh-web-app 来自
全局 dsh 安装；pounding-guard / pounding-sidebar 为 link 依赖；dsh-better-sidebar
走 npm）。profile 根 cordis.yml = []，用户 patch 层 cordis.patch.yml = []
（空 mapping 列表——`- []` 会让 dsh 启动报
"overlay entry must be a mapping"）。
"""
import json
import os

HOME = os.environ["HOME"]
WEB = os.path.join(HOME, ".dsh", "profiles", "web")
os.makedirs(WEB, exist_ok=True)

with open(os.path.join(WEB, "cordis.yml"), "w") as f:
    f.write("[]\n")

with open(os.path.join(WEB, "cordis.patch.yml"), "w") as f:
    f.write("[]\n")

pkg = {
    "name": "dsh-profile-web",
    "private": True,
    "dependencies": {
        "@dsh-external/dsh-pounding-guard": "link:/app/dsh-pounding-guard",
        "pounding-sidebar": "link:/app/pounding-sidebar",
        "dsh-better-sidebar": "^0.13.0",
    },
    "dsh": {
        "profile": {
            "bundles": [
                "@deepseek-ai/dsh-base",
                "@deepseek-ai/dsh-web-app",
                "@dsh-external/dsh-pounding-guard",
                "dsh-better-sidebar",
                "pounding-sidebar",
            ]
        }
    },
}

with open(os.path.join(WEB, "package.json"), "w") as f:
    json.dump(pkg, f, ensure_ascii=False, indent=2)

with open(os.path.join(WEB, "pnpm-workspace.yaml"), "w") as f:
    f.write(
        "packages:\n"
        "  - .\n"
        "\n"
        "nodeLinker: hoisted\n"
        "autoInstallPeers: false\n"
        "allowBuilds:\n"
        "  node-pty: true\n"
    )

print("profile written to", WEB)
