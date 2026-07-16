"""共享 HTTP Session — 连接池复用，避免每请求新建 TCP 连接。

所有 graph 节点应使用此模块的 `session` 替代裸 `requests.post/get`。
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session: requests.Session | None = None


def get_session() -> requests.Session:
    """返回模块级单例 Session，带连接池和重试。"""
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=Retry(total=2, backoff_factor=0.5),
        )
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


# 便捷别名
session = get_session()
