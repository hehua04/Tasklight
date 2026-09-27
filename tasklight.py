#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TaskLight - 轻量级 Windows 任务管理器

以极低开销列出系统进程, 可按 CPU / 内存(工作集) / 已提交内存 等排序,
支持筛选、查看进程详情、结束进程。

依赖:
    pip install psutil

用法:
    python tasklight.py              交互模式 (推荐在 Windows Terminal 下运行)
    python tasklight.py --once       输出一帧后退出, 适合脚本/管道
    python tasklight.py --help       查看全部参数

按键:
    ↑/↓/PgUp/PgDn/Home/End 选择进程   Enter 查看详情
    c/m/p/i/n 按 CPU/内存/提交/PID/名称 排序   Tab 轮换排序列   a 升降序切换
    f 筛选   k 结束选中进程   空格 立即刷新   +/- 调整刷新间隔   q/Esc 退出
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import fnmatch
import math
import re
import shutil
import sys
import threading
import time
import unicodedata
from datetime import datetime
from typing import NamedTuple, Optional

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import psutil
except ImportError:
    sys.stderr.write("缺少依赖 psutil, 请先执行: pip install psutil\n")
    sys.exit(1)

VERSION = "0.2.0"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_k32 = ctypes.WinDLL("kernel32.dll")
_psapi = ctypes.WinDLL("psapi.dll")


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


# ---------------------------------------------------------------------------
# 原生 Windows 采集层
#
# psutil 在 Windows 上逐进程采集: memory_info() / cpu_percent() 各约 6~7ms,
# num_threads() 约 13ms (它每个进程都调一次 CreateToolhelp32Snapshot, 而该调用
# 无论如何都要枚举全系统线程表)。400 个进程合计 5~6 秒, 这正是"加载慢"的根因。
#
# 本层改用两条廉价路径:
#   1) 一次 NtQuerySystemInformation(SystemProcessInformation) 拿回全系统进程的
#      名称 / 线程数 / 句柄数 / 工作集 —— 实测 ~12ms。
#   2) 每进程各一次 OpenProcess(约 8 微秒), 用 GetProcessTimes 取 CPU 时间、
#      用 GetProcessMemoryInfo 取工作集与私有提交 —— 两者共用同一个句柄。
# 合计约 30ms, 与 psutil 数值语义一致 (私有提交 = psutil 的 memory_info().private)。
# ---------------------------------------------------------------------------

class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT),
                ("Buffer", ctypes.c_void_p)]


class _SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    """NtQuerySystemInformation(SystemProcessInformation) 的可见头部 (x64)。

    末尾另有变长线程数组, 本层不使用, 因此不声明。
    """
    _fields_ = [
        ("NextEntryOffset", wt.ULONG),
        ("NumberOfThreads", wt.ULONG),
        ("Reserved1", ctypes.c_ubyte * 48),
        ("ImageName", _UNICODE_STRING),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wt.ULONG),
        ("SessionId", wt.ULONG),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wt.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivatePageCount", ctypes.c_size_t),
    ]


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

    def value(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


_ntdll = ctypes.WinDLL("ntdll.dll")
_ntdll.NtQuerySystemInformation.restype = ctypes.c_ulong
_ntdll.NtQuerySystemInformation.argtypes = [
    ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]

_k32.OpenProcess.restype = wt.HANDLE
_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]

_SYSTEM_PROCESS_INFORMATION_CLASS = 5
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_MAX_PROC_RECORDS = 0x4000
_MAX_THREAD_RECORDS = 0x2000


class Mem(NamedTuple):
    rss: int
    private: Optional[int]
    peak_wset: Optional[int]


class Cpu(NamedTuple):
    cpu_seconds: float
    threads: Optional[int]
    handles: Optional[int]


class _NativeProc(NamedTuple):
    pid: int
    name: str
    mem: Mem
    cpu: Cpu

# NtQuerySystemInformation 返回的 UNICODE_STRING.Buffer 指向的是系统缓冲区,
# 该缓冲区在下次调用时会被复用 —— 必须让缓冲区保持存活, 不能释放后继续读。
_NT_BUF = None
_NT_BUF_SIZE = 1 << 20


def query_processes() -> list:
    """一次系统调用取回全部进程的名称/线程数/句柄数/工作集/峰值工作集/私有提交。

    这里的私有提交取快照的 PagefileUsage —— 与 psutil 的 memory_info().private
    同源 (psutil 也走这个结构), 且在 OpenProcess 被拒的受保护进程上仍然可得。
    """
    global _NT_BUF, _NT_BUF_SIZE
    for _ in range(12):
        if _NT_BUF is None or len(_NT_BUF) < _NT_BUF_SIZE:
            _NT_BUF = ctypes.create_string_buffer(_NT_BUF_SIZE)
        need = wt.ULONG(0)
        st = _ntdll.NtQuerySystemInformation(
            _SYSTEM_PROCESS_INFORMATION_CLASS, _NT_BUF, _NT_BUF_SIZE,
            ctypes.byref(need))
        if st == 0:
            break
        if st == _STATUS_INFO_LENGTH_MISMATCH:
            _NT_BUF_SIZE = max(need.value + (64 << 10), _NT_BUF_SIZE * 2)
            continue
        raise OSError(f"NtQuerySystemInformation 失败: 0x{st:08X}")
    else:
        raise OSError("NtQuerySystemInformation 缓冲区扩容失败")

    out = []
    off = 0
    for _ in range(_MAX_PROC_RECORDS):
        rec = _SYSTEM_PROCESS_INFORMATION.from_buffer(_NT_BUF, off)
        pid = int(rec.UniqueProcessId or 0)
        name = ""
        if rec.ImageName.Buffer:
            name = ctypes.wstring_at(rec.ImageName.Buffer, rec.ImageName.Length // 2)
        nthreads = int(rec.NumberOfThreads)
        if nthreads > _MAX_THREAD_RECORDS:
            raise OSError("进程记录布局异常")
        out.append(_NativeProc(
            pid=pid,
            name=name,
            # 快照自带工作集与 PagefileUsage(即"私有提交"). 这两个值是受保护进程
            # (OpenProcess 被拒) 时唯一可得的来源 —— psutil 也正是从这里取数。
            mem=Mem(rss=int(rec.WorkingSetSize),
                    private=int(rec.PagefileUsage),
                    peak_wset=int(rec.PeakWorkingSetSize)),
            cpu=Cpu(cpu_seconds=0.0, threads=nthreads,
                    handles=int(rec.HandleCount)),
        ))
        nxt = int(rec.NextEntryOffset)
        if not nxt:
            break
        off += nxt
    else:
        raise OSError("进程记录数超出上限")
    return out


def sample_process(pid: int):
    """用同一个句柄取 CPU 时间与内存计数。返回 (cpu_seconds, Mem) 或 (None, None)。"""
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None, None
    try:
        create, exit_, kernel, user = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
        if not _k32.GetProcessTimes(h, ctypes.byref(create), ctypes.byref(exit_),
                                   ctypes.byref(kernel), ctypes.byref(user)):
            return None, None
        cpu_seconds = (kernel.value() + user.value()) / 1e7
        pmc = PROCESS_MEMORY_COUNTERS_EX()
        pmc.cb = ctypes.sizeof(pmc)
        if _psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return cpu_seconds, Mem(rss=int(pmc.WorkingSetSize),
                                    private=int(pmc.PrivateUsage),
                                    peak_wset=int(pmc.PeakWorkingSetSize))
        return cpu_seconds, None
    finally:
        _k32.CloseHandle(h)


def native_available() -> bool:
    """原生采集路径是否可用 (探测失败时上层回退到 psutil)。"""
    try:
        procs = query_processes()
        return len(procs) > 0
    except Exception:
        return False


def get_commit_bytes(pid: int):
    """(已提交内存) 单进程查询; 保留给回退路径使用。"""
    _cpu, mem = sample_process(pid)
    return mem.private if mem else None


def get_commit_charge():
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    if _k32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return int(st.ullTotalPageFile - st.ullAvailPageFile), int(st.ullTotalPageFile)
    return 0, 0


def setup_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    try:
        _k32.SetConsoleOutputCP(65001)
        _k32.SetConsoleCP(65001)
        h = _k32.GetStdHandle(wt.STD_OUTPUT_HANDLE)
        mode = wt.DWORD()
        if h and h != -1 and _k32.GetConsoleMode(h, ctypes.byref(mode)):
            _k32.SetConsoleMode(h, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        pass


def dw(s: str) -> int:
    w = 0
    for ch in _ANSI_RE.sub("", s):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def cut(s: str, width: int) -> str:
    s = _ANSI_RE.sub("", s)
    out, w = [], 0
    for ch in s.replace("\t", " "):
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def fit(s: str, width: int, align: str = "left") -> str:
    s = cut(str(s), width)
    pad = width - dw(s)
    if align == "right":
        return " " * pad + s
    if align == "center":
        left = pad // 2
        return " " * left + s + " " * (pad - left)
    return s + " " * pad


def fmt_bytes(n) -> str:
    if n is None:
        return "-"
    neg = n < 0
    n = float(abs(n))
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            v = f"{n:.0f}{u}" if u == "B" else f"{n:.1f}{u}"
            return ("-" + v) if neg else v
        n /= 1024
    return "-"


class Proc(NamedTuple):
    pid: int
    name: str
    cpu: float
    rss: int
    commit: int | None
    threads: int | None


class Snap(NamedTuple):
    procs: list
    sys_cpu: float
    mem: object
    commit_used: int
    commit_limit: int


SORT_KEYS = ["cpu", "mem", "commit", "name", "pid"]
SORT_LABELS = {
    "cpu": "CPU%",
    "mem": "内存(WS)",
    "commit": "提交",
    "name": "名称",
    "pid": "PID",
}
KEY_TO_SORT = {"c": "cpu", "m": "mem", "p": "commit", "i": "pid", "n": "name"}
COLS = [
    ("pid", "PID", 8, "right"),
    ("name", "名称", 0, "left"),
    ("cpu", "CPU%", 7, "right"),
    ("mem", "内存(WS)", 10, "right"),
    ("commit", "提交", 10, "right"),
    ("threads", "线程", 6, "right"),
]

# 除"名称"外的列宽之和. 名称列宽随终端宽度伸缩, 由它反推:
#   name_w = W - COL_OTHER
# COL_OTHER = 其余各列宽之和 + 列间分隔空格数(6 列共 5 个).
# 表头与数据行都按同一组列宽渲染, 因此两者总宽一致 (均等于 W).
COL_FIXED = sum(w for _k, _l, w, _a in COLS if w)
COL_SEP = len(COLS) - 1
COL_OTHER = COL_FIXED + COL_SEP
MIN_NAME_W = 12

# 右侧安全列: 不要把内容正好铺到终端最后一列。
# 否则最后那个字符会落在末列上, 紧随其后的 \r\n 触发自动换行并把它冲掉 ——
# 表现为最右一列"永远少一个字"(例如表头只剩"线"), 而且拉宽窗口也治不好,
# 因为程序总会重新填满到边界。留 1 列即可规避。
RIGHT_MARGIN = 1


def safe_width(total_width: int) -> int:
    """内容可用的总宽度 = 终端宽度减去右侧安全列 (见 RIGHT_MARGIN)。"""
    return max(total_width - RIGHT_MARGIN, COL_OTHER + MIN_NAME_W)


def name_col_width(total_width: int) -> int:
    """给定终端宽度, 计算名称列宽。表头与数据行必须共用, 否则两者会不等宽。"""
    return max(safe_width(total_width) - COL_OTHER, MIN_NAME_W)


RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
REV = "\x1b[7m"
CYAN = "\x1b[36m"
YEL = "\x1b[33m"
RED = "\x1b[91m"
GRN = "\x1b[92m"


class QuitApp(Exception):
    pass


ARROW_MAP = {"H": "up", "P": "down", "K": "left", "M": "right",
             "G": "home", "O": "end", "I": "pgup", "Q": "pgdn", "S": "del"}
CSI_MAP = {"A": "up", "B": "down", "C": "right", "D": "left",
           "H": "home", "F": "end", "5~": "pgup", "6~": "pgdn"}


def read_key(timeout: float):
    end = time.monotonic() + timeout
    while True:
        if msvcrt and msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                ext = msvcrt.getwch()
                return ARROW_MAP.get(ext, "")
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\r":
                return "enter"
            if ch == "\x08":
                return "back"
            if ch == "\x1b":
                seq = ""
                deadline = time.monotonic() + 0.02
                while msvcrt.kbhit() and time.monotonic() < deadline:
                    seq += msvcrt.getwch()
                if seq.startswith("["):
                    return CSI_MAP.get(seq[1:], "")
                return "esc"
            if ord(ch) >= 32 or ch == "\t":
                return ch
            return ""
        remain = end - time.monotonic()
        if remain <= 0:
            return None
        time.sleep(min(0.02, remain))


class App:
    def __init__(self, args):
        iv = args.interval
        self.interval = max(0.5, min(iv, 30.0)) if math.isfinite(iv) else 2.0
        self.sort_key = args.sort
        self.reverse = args.sort not in ("name", "pid")
        self.filter_str = args.filter or ""
        self.filter_mode = False
        self.plain = args.plain or not sys.stdout.isatty()
        self.once = args.once
        self.selected_pid = None
        self.offset = 0
        self.detail_lines = None
        self.detail_title = ""
        self.pending_kill = None
        self.msg = ""
        self.msg_until = 0.0
        self.ncpu = psutil.cpu_count(True) or 1
        self.native = native_available()
        self._cpu_prev: dict = {}
        self._cpu_prev_t = 0.0
        cu, cl = get_commit_charge()
        self._snap = Snap([], 0.0, psutil.virtual_memory(), cu, cl)
        self.version = 0
        self._stop_evt = threading.Event()
        self._wake_evt = threading.Event()

    @property
    def procs(self):
        return self._snap.procs

    @property
    def sys_cpu(self):
        return self._snap.sys_cpu

    @property
    def mem(self):
        return self._snap.mem

    @property
    def commit_used(self):
        return self._snap.commit_used

    @property
    def commit_limit(self):
        return self._snap.commit_limit

    def set_msg(self, text, ttl=3.5):
        self.msg = text
        self.msg_until = time.monotonic() + ttl

    def prime(self):
        """建立第一次 CPU 基线, 使首帧就能显示真实 CPU% (而不是全 0 的预热帧)。"""
        if self.native:
            self._collect()
        else:
            try:
                psutil.cpu_percent(interval=None)
                for p in psutil.process_iter(attrs=("pid",)):
                    try:
                        p.cpu_percent(interval=None)
                    except Exception:
                        pass
            except Exception:
                pass

    def _collect_native(self):
        """原生路径: 1 次系统调用 + 每进程 2 次廉价句柄查询。实测约 30ms。"""
        now = time.monotonic()
        prev = self._cpu_prev
        dt = now - self._cpu_prev_t if self._cpu_prev_t else 0.0
        cur = {}
        procs = []
        for rec in query_processes():
            cpu_s, mem = sample_process(rec.pid)
            if cpu_s is None:
                pct = 0.0
            else:
                cur[rec.pid] = cpu_s
                old = prev.get(rec.pid)
                if old is None or dt <= 0 or cpu_s < old:
                    pct = 0.0
                else:
                    pct = min((cpu_s - old) / dt / self.ncpu * 100.0, 100.0)
            if mem is not None:
                rss, commit = mem.rss, mem.private
            else:
                # 受保护进程打不开句柄: 用快照值兜底, 避免整行显示 0 / - (与 psutil 口径一致)
                rss, commit = rec.mem.rss, rec.mem.private
            procs.append(Proc(
                pid=rec.pid,
                name=rec.name or "(未知)",
                cpu=pct,
                rss=rss,
                commit=commit,
                threads=rec.cpu.threads,
            ))
        self._cpu_prev = cur
        self._cpu_prev_t = now
        return procs

    def _collect_psutil(self):
        """回退路径 (原生 API 不可用时使用)。"""
        procs = []
        try:
            it = psutil.process_iter(
                attrs=("pid", "name", "memory_info", "cpu_percent", "num_threads"))
            for p in it:
                info = p.info
                pid = info.get("pid") or 0
                mi = info.get("memory_info")
                raw_cpu = info.get("cpu_percent") or 0.0
                procs.append(Proc(
                    pid=int(pid),
                    name=info.get("name") or "(未知)",
                    cpu=min(raw_cpu / self.ncpu, 100.0),
                    rss=int(mi.rss) if mi else 0,
                    commit=get_commit_bytes(int(pid)),
                    threads=info.get("num_threads"),
                ))
        except Exception:
            pass
        return procs

    def _collect(self):
        procs = self._collect_native() if self.native else self._collect_psutil()
        sys_cpu, vm, cu, cl = self.sys_cpu, self.mem, self.commit_used, self.commit_limit
        try:
            sys_cpu = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            cu, cl = get_commit_charge()
        except Exception:
            pass
        return procs, sys_cpu, vm, cu, cl

    def snapshot(self):
        self._snap = Snap(*self._collect())

    def sampler(self):
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                self._snap = Snap(*self._collect())
                self.version += 1
            except Exception:
                pass
            wait = max(0.05, self.interval - (time.monotonic() - t0))
            self._wake_evt.wait(wait)
            self._wake_evt.clear()

    def visible(self):
        fs = self.filter_str.strip().lower()
        rows = [r for r in self.procs if r.pid != 0]
        if fs:
            if "*" in fs or "?" in fs:
                rows = [r for r in rows if fnmatch.fnmatch(r.name.lower(), fs)]
            else:
                rows = [r for r in rows if fs in r.name.lower()]
        if self.sort_key == "name":
            rows = sorted(rows, key=lambda r: r.name.lower(), reverse=self.reverse)
        elif self.sort_key == "pid":
            rows = sorted(rows, key=lambda r: r.pid, reverse=self.reverse)
        elif self.sort_key == "cpu":
            rows = sorted(rows, key=lambda r: r.cpu, reverse=self.reverse)
        elif self.sort_key == "mem":
            rows = sorted(rows, key=lambda r: r.rss, reverse=self.reverse)
        else:
            rows = sorted(rows, key=lambda r: r.commit or 0, reverse=self.reverse)
        return rows

    def sel_index(self, rows):
        if self.selected_pid is None:
            return 0 if rows else -1
        for i, r in enumerate(rows):
            if r.pid == self.selected_pid:
                return i
        return 0 if rows else -1

    def move_sel(self, delta):
        rows = self.visible()
        idx = self.sel_index(rows)
        if idx < 0:
            return
        idx = max(0, min(len(rows) - 1, idx + delta))
        self.selected_pid = rows[idx].pid

    def set_sort(self, key):
        if key not in SORT_KEYS:
            return
        self.sort_key = key
        self.reverse = key not in ("name", "pid")

    def cycle_sort(self, step):
        cur = SORT_KEYS.index(self.sort_key)
        self.set_sort(SORT_KEYS[(cur + step) % len(SORT_KEYS)])

    def do_kill(self, pid):
        try:
            psutil.Process(pid).kill()
            self.set_msg(f"已发送终止信号 -> PID {pid}")
        except psutil.AccessDenied:
            self.set_msg("拒绝访问: 请以管理员身份运行")
        except psutil.NoSuchProcess:
            self.set_msg(f"PID {pid} 已不存在")
        except Exception as e:
            self.set_msg(f"结束失败: {e}")

    def open_detail(self, rec: Proc):
        fields: list[tuple[str, str]] = []

        def add(label, fn, fmt=None):
            try:
                v = fn()
                fields.append((label, fmt(v) if fmt else str(v)))
            except Exception:
                fields.append((label, "-"))

        try:
            p = psutil.Process(rec.pid)
        except psutil.NoSuchProcess:
            self.set_msg("进程已退出")
            return
        add("名称", p.name)
        add("PID", lambda: rec.pid)
        add("状态", p.status)

        def parent_str():
            ppid = p.ppid()
            try:
                return f"{ppid} ({psutil.Process(ppid).name()})"
            except Exception:
                return str(ppid)

        add("父进程", parent_str)
        add("创建时间", lambda: datetime.fromtimestamp(p.create_time()).strftime("%Y-%m-%d %H:%M:%S"))
        add("用户名", p.username)
        add("可执行文件", p.exe)
        add("命令行", lambda: " ".join(p.cmdline()))
        add("线程数", p.num_threads)
        add("句柄数", p.num_handles)
        add("优先级(Nice)", p.nice)

        # 优先用一次句柄查询拿 (工作集, 峰值工作集, 已提交); 打不开时退回
        # rec 里已经采集好的值 —— 与列表显示保持同一口径, 也避免 psutil 的多次慢调用
        _cpu, mem = sample_process(rec.pid) if self.native else (None, None)
        rss = mem.rss if mem is not None else rec.rss
        peak = mem.peak_wset if mem is not None else None
        commit = mem.private if mem is not None else rec.commit
        add("工作集(物理)", lambda: fmt_bytes(rss))
        add("峰值工作集", lambda: fmt_bytes(peak))
        add("已提交内存", lambda: fmt_bytes(commit))
        add("IO读字节", lambda: fmt_bytes(p.io_counters().read_bytes))
        add("IO写字节", lambda: fmt_bytes(p.io_counters().write_bytes))
        self.detail_title = f"进程详情  PID {rec.pid}  {rec.name}"
        self.detail_lines = fields

    def title_line(self, W):
        used = self.mem.total - self.mem.available
        parts = [f"{BOLD}{GRN}TaskLight{RESET} v{VERSION}",
                 f"进程 {len(self.procs)}"]
        cc = BOLD + RED if self.sys_cpu >= 80 else (BOLD + YEL if self.sys_cpu >= 50 else "")
        parts.append(f"CPU {cc}{self.sys_cpu:.1f}%{RESET}")
        mc = BOLD + RED if self.mem.percent >= 90 else (BOLD + YEL if self.mem.percent >= 75 else "")
        parts.append(f"内存 {mc}{fmt_bytes(used)}/{fmt_bytes(self.mem.total)}"
                     f"({self.mem.percent:.0f}%){RESET}")
        parts.append(f"提交 {fmt_bytes(self.commit_used)}/{fmt_bytes(self.commit_limit)}")
        line = " │ ".join(parts)
        if self.pending_kill:
            line = f"{BOLD}{RED}确认结束 PID {self.pending_kill[0]} ({self.pending_kill[1]})? y=确认 其他=取消{RESET}"
        elif self.msg and time.monotonic() < self.msg_until:
            line = f"{BOLD}{YEL}{self.msg}{RESET}"
        return fit(line, safe_width(W))

    def header_line(self, rows_n, W):
        name_w = name_col_width(W)
        cells = []
        for key, label, width, align in COLS:
            w = width if width else name_w
            lab = label + (" ↓" if key == self.sort_key and self.reverse else
                           " ↑" if key == self.sort_key else "")
            cells.append(fit(lab, w, align))
        row = " ".join(cells)
        note = f"共 {rows_n} 项"
        pad = W - dw(row) - dw(note) - 2
        if pad >= 2:
            return CYAN + BOLD + row + RESET + " " * (pad + 2) + DIM + note + RESET
        # 放不下"共 N 项"时不能再对 row 做 fit: row 本来就是 W - RIGHT_MARGIN 宽,
        # 而 fit 是左对齐"补齐到 W", 会把这一列重新填满并吃掉最右一个字符
        # (表现为表头比数据行宽 1 列、最右列少一个字)。直接返回即可。
        return CYAN + BOLD + row + RESET

    def row_line(self, r: Proc, name_w, selected):
        cpu_s = f"{r.cpu:.1f}"
        cc = BOLD + RED if r.cpu >= 80 else (BOLD + YEL if r.cpu >= 40 else "")
        memc = BOLD + YEL if r.rss >= (1 << 30) else ""
        # 列宽必须与 header_line 取自同一处 (COLS), 否则表头与数据行会不等宽
        width = {key: (w if w else name_w) for key, _l, w, _a in COLS}
        cells = [
            fit(r.pid, width["pid"], "right"),
            fit(r.name, width["name"]),
            fit(cpu_s, width["cpu"], "right"),
            fit(fmt_bytes(r.rss), width["mem"], "right"),
            fit(fmt_bytes(r.commit), width["commit"], "right"),
            fit("-" if r.threads is None else r.threads, width["threads"], "right"),
        ]
        colors = ["", "", cc, memc, memc, DIM]
        if selected:
            return REV + " ".join(cells) + RESET
        out = []
        for cell, color in zip(cells, colors):
            out.append(color + cell + RESET if color else cell)
        # 与 header_line 一样用空格分隔各列, 否则数据行会比表头短 (列会错位)
        return " ".join(out)

    def help_line(self, W):
        if self.filter_mode:
            return fit(f"{BOLD}筛选: {self.filter_str}▌{RESET}  回车=应用 Esc=清空并退出", W)
        hint = ("[↑↓]选择 [Enter]详情 [c/m/p/i/n]排序 [Tab]轮换 [a]升降序 "
                "[f]筛选 [k]结束 [空格]刷新 [+/-]间隔 [q]退出")
        if self.filter_str:
            hint = f"筛选: {BOLD}{CYAN}{self.filter_str}{RESET}  " + hint
        return fit(hint, W)

    def build_frame(self, W, H):
        lines = []
        SW = safe_width(W)   # 所有整行内容用这个宽度, 避免占用最后一列
        if self.detail_lines is not None:
            lines.append(BOLD + CYAN + fit(f"── {self.detail_title}", SW) + RESET)
            lines.append(DIM + "─" * SW + RESET)
            vw = max(W - 18, 10)
            max_show = H - 3
            for label, val in self.detail_lines:
                first = True
                while len(lines) < max_show:
                    seg = cut(val, vw)
                    if not seg and dw(val) > 0:
                        break
                    prefix = fit(label, 16) if first else " " * 16
                    lines.append(prefix + seg)
                    first = False
                    val = val[len(seg):].strip()
                    if dw(val) <= 0:
                        break
            while len(lines) < max_show:
                lines.append(" " * SW)
            lines.append(DIM + "按任意键返回列表" + RESET)
            return lines[:H]

        body_h = max(H - 3, 3)
        rows = self.visible()
        name_w = name_col_width(W)
        idx = self.sel_index(rows)
        if not rows:
            self.offset = 0
        elif idx >= 0:
            if idx < self.offset:
                self.offset = idx
            elif idx >= self.offset + body_h:
                self.offset = idx - body_h + 1
            self.offset = max(0, min(self.offset, max(len(rows) - body_h, 0)))
        lines.append(self.title_line(W))
        lines.append(self.header_line(len(rows), W))
        for r in rows[self.offset:self.offset + body_h]:
            selected = (r.pid == self.selected_pid)
            lines.append(self.row_line(r, name_w, selected))
        shown = min(len(rows) - self.offset, body_h)
        for _ in range(max(body_h - shown, 0)):
            lines.append(" " * SW)
        lines.append(self.help_line(SW))
        return lines[:H]

    def render(self):
        W, H = shutil.get_terminal_size((120, 32))
        lines = self.build_frame(W, H)
        if self.plain:
            sys.stdout.write("\n".join(_ANSI_RE.sub("", l).rstrip() for l in lines) + "\n")
            sys.stdout.flush()
        else:
            frame = RESET + "\x1b[K\r\n".join(lines) + RESET + "\x1b[K\x1b[J"
            sys.stdout.write("\x1b[H\x1b[?25l" + frame)
            sys.stdout.flush()

    def handle_key(self, key):
        if self.filter_mode:
            if key in ("enter",):
                self.filter_mode = False
            elif key == "esc":
                self.filter_mode = False
                self.filter_str = ""
            elif key == "back":
                self.filter_str = self.filter_str[:-1]
            elif len(key) == 1 and ord(key) >= 32:
                self.filter_str += key
            return
        if self.pending_kill:
            if key.lower() == "y":
                self.do_kill(self.pending_kill[0])
            self.pending_kill = None
            return
        if self.detail_lines is not None:
            self.detail_lines = None
            return
        low = key.lower()
        if low == "q":
            raise QuitApp
        if key == "esc":
            if self.filter_str or self.filter_mode:
                self.filter_str = ""
                self.filter_mode = False
            else:
                raise QuitApp
        if key == "up":
            self.move_sel(-1)
        elif key == "down":
            self.move_sel(1)
        elif key == "pgup":
            self.move_sel(-10)
        elif key == "pgdn":
            self.move_sel(10)
        elif key == "home":
            rows = self.visible()
            if rows:
                self.selected_pid = rows[0].pid
        elif key == "end":
            rows = self.visible()
            if rows:
                self.selected_pid = rows[-1].pid
        elif key == "enter":
            rows = self.visible()
            idx = self.sel_index(rows)
            if idx >= 0:
                self.open_detail(rows[idx])
        elif key == "\t":
            self.cycle_sort(1)
        elif key == "left":
            self.cycle_sort(-1)
        elif key == "right":
            self.cycle_sort(1)
        elif low in KEY_TO_SORT:
            self.set_sort(KEY_TO_SORT[low])
        elif low == "a":
            self.reverse = not self.reverse
        elif low == "f":
            self.filter_mode = True
        elif low == "k" or key == "del":
            rows = self.visible()
            idx = self.sel_index(rows)
            if idx >= 0:
                r = rows[idx]
                self.pending_kill = (r.pid, r.name)
        elif key in ("+", "="):
            self.interval = min(self.interval + 0.5, 15.0)
        elif key == "-":
            self.interval = max(self.interval - 0.5, 0.5)
        elif key == " ":
            self._wake_evt.set()


def parse_args():
    ap = argparse.ArgumentParser(
        prog="tasklight",
        description="TaskLight - 轻量级 Windows 任务管理器",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("-t", "--interval", type=float, default=2.0,
                    help="刷新间隔(秒)")
    ap.add_argument("-s", "--sort", choices=SORT_KEYS, default="mem",
                    help="初始排序字段: cpu/mem/commit/name/pid")
    ap.add_argument("-f", "--filter", default="", help="按进程名初始筛选, 支持 * ? 通配")
    ap.add_argument("--once", action="store_true", help="输出一帧后退出(适合脚本)")
    ap.add_argument("--plain", action="store_true",
                    help="禁用 ANSI 颜色与整屏模式(重定向或旧终端时使用)")
    ap.add_argument("--version", action="version", version=f"TaskLight {VERSION}")
    return ap.parse_args()


def main():
    setup_console()
    app = App(parse_args())
    if not app.plain:
        sys.stdout.write("\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l")
        sys.stdout.flush()
    try:
        app.prime()
        if app.once:
            time.sleep(min(max(app.interval / 4, 0.5), 1.5))
            app.snapshot()
            app.render()
        else:
            th = threading.Thread(target=app.sampler, daemon=True)
            th.start()
            last_ver = -1
            while True:
                key = read_key(0.04)
                dirty = False
                if key is not None:
                    app.handle_key(key)
                    dirty = True
                if app.version != last_ver:
                    last_ver = app.version
                    if not any(r.pid == app.selected_pid for r in app.procs):
                        app.selected_pid = None
                    if app.selected_pid is None and app.procs:
                        rows = app.visible()
                        if rows:
                            app.selected_pid = rows[0].pid
                    dirty = True
                if dirty:
                    app.render()
    except (QuitApp, KeyboardInterrupt):
        pass
    finally:
        app._stop_evt.set()
        if not app.plain:
            sys.stdout.write("\x1b[?25h\x1b[0m\x1b[?1049l")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
