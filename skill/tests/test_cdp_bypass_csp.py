# skill/tests/test_cdp_bypass_csp.py
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts.lib.cdp_client import CdpTab  # noqa: E402


def test_set_bypass_csp_sends_command():
    tab = CdpTab.__new__(CdpTab)          # 跳过 __init__ 的连接依赖
    sent = {}
    tab._send = lambda method, params=None, msg_id=None: sent.update(
        method=method, params=params) or 1
    tab._recv_until_id = lambda mid, timeout=None: {"result": {}}
    tab.set_bypass_csp()
    assert sent == {"method": "Page.setBypassCSP", "params": {"enabled": True}}


def test_set_bypass_csp_failure_warns_not_raises():
    tab = CdpTab.__new__(CdpTab)
    tab._send = mock.MagicMock(side_effect=OSError("gone"))
    tab.set_bypass_csp()  # 不抛
