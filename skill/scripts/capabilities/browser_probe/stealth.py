# 反检测 JS 脚本：在页面加载时注入（v0.28.7 极简化）

# ⚠️ v0.28.7 重写理由（2026-08-07 实测实证）：
# 工具 Chrome 跑在【用户真实电脑】上，是真实 Chrome —— 真实指纹天然干净：
#   - webdriver=false（chrome_launcher 启动参数 --disable-blink-features=AutomationControlled 已处理）
#   - hardwareConcurrency/deviceMemory/plugins/languages = 真实值（无需伪造，伪造必与真实硬件不符）
# 旧版 stealth 把真实值全部改成伪造值（webdriver→undefined、核数→随机4/8、内存→4G、
# 凭空造 chrome.runtime/connection、languages 加 en-US）—— 每个都是检测信号，有害无益。
# 实测对比（真实 Chrome vs 旧 stealth 注入后）：
#   webdriver: false → undefined(标准检测一眼识破) | hardwareConcurrency: 12 → 8(不符)
#   deviceMemory: 32 → 4(8倍差距) | plugins: 5 → 3 | chrome.runtime: false → true(新检测点)
# 1688 当前未拦是运气，升级检测后旧 stealth 反而更容易被抓。
#
# 现仅保留唯一真实差异的处理（防御性，即使 flag 已覆盖也兜底）：
STEALTH_JS = """
(() => {
    // navigator.webdriver — 唯一由 CDP 自动化引入的真实差异
    // 真实 Chrome 为 false；--disable-blink-features=AutomationControlled 已覆盖，
    // 这里做兜底（返回 false，绝不返回 undefined）
    Object.defineProperty(navigator, 'webdriver', {
        get: () => false,
        configurable: true,
    });
})()
"""
