# -*- coding: utf-8 -*-
"""TaskLight 采集性能与保真度回归测试。期望: 全部 PASS。"""
from __future__ import annotations
import statistics
import sys
import time

sys.argv = ["tasklight.py"]
import tasklight as T

FAIL = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(label)


print("=" * 62)
print("1. 原生采集路径可用性")
print("=" * 62)
check("native_available()", T.native_available())

if not T.native_available():
    print("\n原生路径不可用, 后续测试无意义")
    sys.exit(1)

print()
print("=" * 62)
print("2. 单次采集延迟 (阈值 500 ms)")
print("=" * 62)
a = T.App(type("A", (), dict(interval=2.0, sort="mem", filter="", plain=True, once=False))())
ts = []
for _ in range(5):
    t0 = time.perf_counter()
    snap = T.Snap(*a._collect())
    ts.append(time.perf_counter() - t0)
med = statistics.median(ts)
check("中位采集耗时 < 500 ms", med < 0.5, f"实测 {med*1000:.1f} ms (上限 500 ms)")
check("有进程数据", len(snap.procs) > 20, f"{len(snap.procs)} 个进程")

print()
print("=" * 62)
print("3. 字段完整性")
print("=" * 62)
n = len(snap.procs)
n_commit = sum(1 for r in snap.procs if r.commit is not None)
n_thr = sum(1 for r in snap.procs if r.threads is not None)
# 注意: 工作集(WS) 为 0 是合法值 —— 完全换出到页面文件的进程确实是 0,
# psutil 在同名进程上同样报 0 (实测 62/62 一致)。所以这里不能用 rss > 0 衡量
# 是否采集成功, 否则会随系统内存压力随机变红。改用"字段是否取到"来判断。
n_fields = sum(1 for r in snap.procs
               if r.commit is not None and r.threads is not None and r.rss >= 0)
check("commit 列填充率 > 95%", n_commit > n * 0.95, f"{n_commit}/{n}")
check("线程列填充率 > 95%", n_thr > n * 0.95, f"{n_thr}/{n}")
check("三字段均取到的进程 > 95%", n_fields > n * 0.95, f"{n_fields}/{n}")
# 注意: 不能拿"工作集非 0 的进程数"与 psutil 比 —— 完全换出的进程工作集就是 0,
# 且会随内存压力在两次采样之间来回变化, 这种比较必然随机变红。
# 工作集口径的正确性由下面的"逐进程交替测量"来保证。
check("PID 0 不出现在可见列表",
      all(r.pid != 0 for r in a.visible()), "")

# 与 psutil 交叉校验工作集口径。
# 注意: psutil 逐进程采集本身要 6 秒, 拿它当"同一瞬间"的参照会把内存漂移算成误差,
# 所以这里对每个进程做"原生 -> psutil"紧邻的交替测量, 并且只统计有实际大小的进程。
import psutil as _ps
_pairs = []
for r in snap.procs:
    if r.rss < 1024 * 1024:
        continue
    _c, _mem = T.sample_process(r.pid)
    _native = _mem.rss if _mem is not None else r.rss
    try:
        _ref = _ps.Process(r.pid).memory_info().rss
    except Exception:
        continue
    if _ref > 0:
        _pairs.append((abs(_native - _ref) / _ref, r.name))
_devs = sorted(d for d, _ in _pairs)
if _devs:
    _med = _devs[len(_devs) // 2]
    _p95 = _devs[int(len(_devs) * 0.95)] if len(_devs) > 1 else _devs[0]
    check("工作集与 psutil 口径一致(中位偏差<10%)", _med < 0.10,
          f"n={len(_devs)} 中位 {_med*100:.1f}% p95 {_p95*100:.1f}%")
else:
    check("工作集与 psutil 口径一致(中位偏差<10%)", False, "没有可比样本")

print()
print("=" * 62)
print("3b. 表格布局 (表头与数据行必须等宽对齐)")
print("=" * 62)
# 这两条断言守的是实际踩过的坑: row_line 曾用固定列宽且不用空格分隔各列,
# 导致数据行比表头短、PID 与进程名粘连。
_layout_fail = []
for _W in (60, 80, 100, 113, 120, 160):
    _nw = T.name_col_width(_W)
    _hdr = a.header_line(len(snap.procs), _W)
    _row = a.row_line(snap.procs[0], _nw, False)
    _hw, _rw = T.dw(_hdr), T.dw(_row)
    if _hw != _rw:
        _layout_fail.append(f"W={_W} 表头{_hw}≠数据{_rw}")
    _pid_w = T.COLS[0][2]
    if len(_row) > _pid_w and _row[_pid_w] != " ":
        _layout_fail.append(f"W={_W} PID与名称粘连")
check("表头与数据行等宽且列分隔正确", not _layout_fail,
      "; ".join(_layout_fail) if _layout_fail else "6 种宽度全部对齐")
# 内容宽度应为 safe_width(W) = W - RIGHT_MARGIN, 即右侧留出安全列,
# 否则末列字符会被 \r\n 的自动换行冲掉
check("右侧留出安全列 (内容宽 = W - RIGHT_MARGIN)", all(
    T.dw(a.header_line(len(snap.procs), w)) == T.safe_width(w)
    for w in (80, 100, 113, 120)),
    f"80/100/113/120 均留 {T.RIGHT_MARGIN} 列")
check("极窄终端下名称列仍有最小宽度", T.name_col_width(10) >= T.MIN_NAME_W,
      f"name_col_width(10)={T.name_col_width(10)}")
# build_frame 里每一行都不能超过安全宽度, 否则末列会被 \r\n 的自动换行吃掉。
# 阈值必须用 T.safe_width(_W) 本身, 它带下限 (极窄终端下 >= COL_OTHER+MIN_NAME_W),
# 不能写成 _W - RIGHT_MARGIN, 否则窄终端下会误报。
_over = []
for _W, _H in ((60, 20), (80, 24), (100, 30), (113, 32), (140, 40)):
    _limit = T.safe_width(_W)
    for _ln in a.build_frame(_W, _H):
        if T.dw(_ln) > _limit:
            _over.append(f"W={_W} 行宽{T.dw(_ln)}>{_limit}")
check("整帧所有行都不超过安全宽度", not _over,
      "; ".join(sorted(set(_over))[:4]) if _over else "5 种尺寸全部安全")

print()
print("=" * 62)
print("4. CPU% 正确性 (两次采样后应有非零值)")
print("=" * 62)
s1 = T.Snap(*a._collect())
time.sleep(1.0)
s2 = T.Snap(*a._collect())
nonzero = [r for r in s2.procs if r.cpu > 0]
check("存在非零 CPU 进程", len(nonzero) > 0, f"{len(nonzero)} 个")
check("CPU% 都在 [0,100]", all(0.0 <= r.cpu <= 100.0 for r in s2.procs))
top = sorted(s2.procs, key=lambda r: -r.cpu)[:3]
print(f"        CPU top3: {[(r.name[:20], round(r.cpu, 1)) for r in top]}")
# 与 sys_cpu 的一致性: 所有进程 CPU% 之和不应远超 100%
total_pct = sum(r.cpu for r in s2.procs)
check("全部进程 CPU% 之和 < 100*ncpu",
      total_pct < 100 * a.ncpu, f"合计 {total_pct:.1f}% / 上限 {100*a.ncpu}%")
check("系统 CPU 在 [0,100]", 0.0 <= s2.sys_cpu <= 100.0, f"{s2.sys_cpu:.1f}%")

print()
print("=" * 62)
print("5. psutil 回退路径仍然可用")
print("=" * 62)
t0 = time.perf_counter()
fb = a._collect_psutil()
el = time.perf_counter() - t0
assert isinstance(fb, list), f"_collect_psutil 应返回 list, 实为 {type(fb).__name__}"
if fb:
    assert not isinstance(fb[0], (list, tuple)) or hasattr(fb[0], "_fields"), \
        "_collect_psutil 的元素应为 Proc"
# 注意: fb 本身是进程列表, 不能用 len(fb[0]) —— 那是 Proc 的字段数(恒为 6)
check("回退路径返回数据", len(fb) > 20, f"{len(fb)} 个进程, 耗时 {el*1000:.0f} ms")

print()
print("=" * 62)
print("6. prime + 首帧 (模拟 --once)")
print("=" * 62)
b = T.App(type("A", (), dict(interval=2.0, sort="mem", filter="", plain=True, once=False))())
t0 = time.perf_counter()
b.prime()
b.snapshot()
el = time.perf_counter() - t0
first_nonzero = sum(1 for r in b.procs if r.cpu > 0)
check("prime+snapshot < 2 s", el < 2.0, f"实测 {el*1000:.0f} ms")
check("首帧即有非零 CPU", first_nonzero > 0, f"{first_nonzero} 个进程 CPU>0")

print()
print("=" * 62)
print(f"结果: {'全部通过' if not FAIL else '失败 ' + str(FAIL)}")
print("=" * 62)
sys.exit(1 if FAIL else 0)
