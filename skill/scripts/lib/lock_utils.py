#!/usr/bin/env python3
"""跨平台进程级文件锁（POSIX fcntl.flock / Windows msvcrt.locking 双实现）。

来龙去脉（fix/skill-concurrency-v1 批1 T1，方案 §A3）：
原实现散在 chrome_launcher.py 的 `_try_acquire_lock`/`_release_lock`（Q15 引入的
per-profile Chrome 启动锁）。T1 把它提炼为通用模块——同一份实现同时服务
①chrome_launcher 的 per-profile 启动锁（薄转发保持原函数名，零调用方破坏）与
②cli.py 重命令串行闸（heavy_cdp.lock，锁文件写 {pid, cmd, started_at} 供报错
展示占用方）。**改锁语义前先读本模块头注释。**

语义要点：
- flock 是**建议锁**：互斥只对同样走本模块的协作方生效；锁文件残留无害，
  进程退出 OS 自动释放（fd 关闭即放锁），无需显式清理路径。
- **同进程不同 fd 再抢同一锁同样互斥**（flock 按 open file description 记账）——
  进程内嵌套使用方（如 cli 重命令闸）必须自己做可重入标志，勿依赖 flock 重入。
- 与原 chrome_launcher 版本的唯一差异：open 模式 "w"（truncate）→ "a+"。
  闸场景下后来者失败重试时会以 "w" 把持有者刚写入的占用方信息截掉，报错
  展示「谁占着」就失效了；"a+" 不截断，写侧由调用方 seek(0)+truncate 自己管理。
  chrome_launcher 调用方从不读写锁内容，行为零变化。
"""
from __future__ import annotations

import platform
import time
from pathlib import Path
from typing import IO, Optional

# Cross-platform file locking
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]
    import fcntl

# msvcrt.locking 的 LK_NBLCK / LK_UNLCK 常量（无条件定义，便于非 Windows 平台
# mock msvcrt 验证调用路径）；取值见 WinBase.h。
_LOCK_NBEX = 0x00000002  # _LK_NBLCK: 非阻塞尝试（循环控制等待时机）
_LOCK_UN = 0x00000000


def try_acquire(lock_path: Path | str, timeout: float = 30.0) -> Optional[IO]:
    """阻塞获取排他锁, 最长等待 timeout 秒。

    成功 → 返回打开的锁文件 fd; 超时/失败 → None（调用方降级, 不抛异常）。
    timeout=0 即单次非阻塞尝试（fail-fast 场景用）。
    进程退出时 OS 自动释放锁, 锁文件残留无害。
    """
    try:
        # "a+" 而非 "w"：不截断——并发失败重试不得抹掉持有者写入的占用方信息
        # （详见模块头注释「与原实现的唯一差异」）。
        fd = open(lock_path, "a+")
    except OSError:
        return None
    deadline = time.monotonic() + timeout
    while True:
        try:
            if platform.system() == "Windows":
                fd.seek(0)
                msvcrt.locking(fd.fileno(), _LOCK_NBEX, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (OSError, BlockingIOError):
            if time.monotonic() >= deadline:
                try:
                    fd.close()
                except OSError:
                    pass
                return None
            time.sleep(0.1)


def release(fd) -> None:
    """释放锁并关闭 fd。失败静默（OS 在进程退出时兜底释放）。"""
    if fd is None:
        return
    try:
        if platform.system() == "Windows":
            fd.seek(0)
            msvcrt.locking(fd.fileno(), _LOCK_UN, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fd.close()
    except Exception:
        pass


def write_holder_info(fd, info: str) -> None:
    """把占用方描述写入锁文件（拿到锁之后调用；失败静默——展示用途不阻塞主流程）。

    锁文件从此有了内容语义：后来者 try_acquire 失败时可 read_holder_info
    读出「谁占着」（pid/命令/时长），人话报错全靠它。
    """
    try:
        fd.seek(0)
        fd.truncate()
        fd.write(info)
        fd.flush()
    except Exception:
        pass


def read_holder_info(lock_path: Path | str) -> Optional[str]:
    """读锁文件的占用方描述（建议锁不拦读，持有期间随时可读）。

    锁文件不存在 / 空 / 脏数据 → None（不抛异常）。展示用途，绝不做正确性判定。
    """
    try:
        raw = Path(lock_path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return raw or None


class _FileLock:
    """file_lock 上下文管理器实现（yield fd；未拿到时 fd 为 None，调用方自辨）。"""

    def __init__(self, lock_path: Path | str, timeout: float = 30.0, blocking: bool = True):
        self._path = lock_path
        self._timeout = timeout
        self._blocking = blocking
        self.fd: Optional[IO] = None

    def __enter__(self) -> Optional[IO]:
        # blocking=False → timeout=0 单次非阻塞尝试，拿不到立即 yield None。
        self.fd = try_acquire(self._path, timeout=self._timeout if self._blocking else 0.0)
        return self.fd

    def __exit__(self, exc_type, exc, tb) -> bool:
        release(self.fd)
        self.fd = None
        return False


def file_lock(lock_path: Path | str, timeout: float = 30.0, blocking: bool = True) -> _FileLock:
    """文件锁上下文管理器：`with file_lock(p) as fd:` — fd 非 None 即持有。

    blocking=True（缺省）阻塞等待至多 timeout 秒；blocking=False 拿不到立即
    yield None。退出时自动 release（fd 为 None 时 release 幂等）。
    """
    return _FileLock(lock_path, timeout=timeout, blocking=blocking)
