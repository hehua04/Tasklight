# TaskLight 轻量级 Windows 任务管理器

一个单文件、低开销的进程查看器，用来替代"打开慢、偶尔卡住"的 Windows 任务管理器。
可按 CPU / 内存（工作集）/ 已提交内存 等排序浏览全部进程，并支持筛选、查看详情与结束进程。

```
TaskLight v0.2.0 │ 进程 401 │ CPU 8.0% │ 内存 10.6GB/15.2GB(70%) │ 提交 27.3GB/33.3GB
     PID 名称                                           CPU% 内存(WS) ↓       提交   线程
    4900 Memory Compression                              0.0    836.0MB      9.2MB     79
   42920 msedge.exe                                      3.2    698.9MB    739.7MB     28
    4864 node.exe                                        0.0    381.3MB    643.8MB     14
    6172 avp.exe                                         0.0    279.9MB    428.6MB    142
   15104 explorer.exe                                    0.0    215.0MB    334.9MB    169
   17664 msedge.exe                                      1.6    206.1MB    805.6MB    123
  ...
[↑↓]选择 [Enter]详情 [c/m/p/i/n]排序 [Tab]轮换 [a]升降序 [f]筛选 [k]结束 [空格]刷新 [+/-]间隔 [q]退出
```

## 功能特性

- **轻量**：单个 `.py` 文件，秒开，常驻内存仅十几 MB
- **快照采集约 20ms**：一次 `NtQuerySystemInformation` 取回全部进程的名称/线程数/句柄数/工作集，
  再用 `GetProcessTimes` + `GetProcessMemoryInfo` 补齐 CPU 与提交内存（详见下方"性能"）
- **列信息**：PID、名称、CPU%（相对整机归一化）、内存（工作集/物理占用）、已提交（私有）内存、线程数
- **顶栏总览**：系统 CPU 占用、物理内存用量/占比、提交内存用量/上限、进程总数
- **排序**：任意列排序，`Tab` 或 `←→` 轮换排序列，`a` 切换升降序
- **筛选**：按进程名实时过滤，支持 `*` `?` 通配符
- **详情视图**：命令行、可执行文件路径、父进程、创建时间、用户名、句柄数、IO 字节数等
- **结束进程**：选中后一键终止（需确认），权限不足会给出提示
- **脚本友好**：`--once` 模式输出一帧即退出，方便配合其他工具使用
- **自动回退**：原生 API 不可用时自动退回 psutil 采集路径，功能不变（只是变慢）

## 环境要求

- Windows 10 及以上（需要支持 ANSI/VT 的终端，推荐 Windows Terminal）
- Python 3.8+
- psutil ≥ 7.2.2

## 安装运行

```powershell
pip install -r requirements.txt
python tasklight.py
```

或者直接双击 `start.bat`（自动检查并安装依赖）。

**放到桌面独立使用**：双击项目里的 `install.bat`，会自动在桌面生成 `TaskLight.bat`
（自动探测真实桌面路径，兼容 OneDrive 重定向）。
也可以手动把 `start.bat` 复制到其他位置，但它需要与 `tasklight.py` 同目录才能工作。

## 快捷键

| 按键 | 功能 |
| --- | --- |
| `↑` `↓` / `PgUp` `PgDn` / `Home` `End` | 移动选择 |
| `Enter` | 打开选中进程详情（详情页按任意键返回） |
| `c` / `m` / `p` / `i` / `n` | 按 CPU% / 内存 / 已提交 / PID / 名称 排序 |
| `Tab` / `←` `→` | 轮换排序列 |
| `a` | 升序 ⇄ 降序 |
| `f` | 输入筛选词，`回车` 应用，`Esc` 清空 |
| `k` 或 `Del` | 强制结束选中的进程，按 `y` 确认 |
| `空格` | 立即刷新 |
| `+` / `-` | 调整自动刷新间隔（0.5s ~ 15s，`=` 等效 `+`） |
| `q` / `Esc` | 退出 |

## 命令行参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-t`, `--interval` | `2.0` | 自动刷新间隔（秒） |
| `-s`, `--sort` | `mem` | 初始排序：`cpu` / `mem` / `commit` / `name` / `pid` |
| `-f`, `--filter` | 空 | 启动时按进程名筛选，支持通配符，如 `-f chrome*` |
| `--once` | 关 | 只输出一帧后退出（适合重定向到文件或管道） |
| `--plain` | 关 | 关闭 ANSI 颜色与整屏模式，兼容旧终端 |

示例：

```powershell
python tasklight.py -s cpu -t 1          # 按 CPU 排序，每秒刷新
python tasklight.py -s commit -f msedge  # 按已提交内存排序，只看 Edge
python tasklight.py --once > procs.txt   # 导出当前快照
```

## 说明

- **已提交内存**：取自系统进程快照的 `PagefileUsage`（私有提交字节），与任务管理器"详细信息"页中的"提交大小"一致，也与 `psutil` 的 `memory_info().private` 同源；顶栏的提交总量/上限来自系统全局统计。
- **权限**：普通权限下即可查看所有进程的内存与线程数——受保护的系统进程（如 MemCompression、MsMpEng、dwm 等）虽然 `OpenProcess` 会被拒，但其工作集与提交内存可以从系统快照里直接读到，因此这两列**不再显示 `-`**；只有需要读句柄的字段（详情的命令行、IO 字节数等）才会因权限不足而缺失，此时请以管理员身份运行。
- **工作集为 0 是正常的**：完全换出到页面文件的进程工作集确实为 0，`psutil` 也报同样的值。
- **首次显示**：程序启动时会先取一次基线快照，因此**首帧就显示真实 CPU%**，不再有全 0 的预热帧。

## 性能

采集全部进程只需**约 20ms**（本机 400+ 进程实测中位数 16~18ms），因此刷新间隔是真实生效的。

早期版本曾用 psutil 的逐进程接口采集，在进程/线程较多的机器上会退化得很严重，原因是：

| 采集方式 | 单进程开销 | 全量（约 400 进程） |
| --- | --- | --- |
| `psutil` `memory_info()` / `cpu_percent()` | 约 6~7 ms | 约 2.4 s |
| `psutil` `num_threads()` | 约 13 ms | 约 5.2 s |
| 原生 `GetProcessTimes` + `GetProcessMemoryInfo`（同一句柄） | 约 0.008 ms | 约 3 ms |
| 原生一次 `NtQuerySystemInformation` | — | 约 12 ms |

`psutil` 的 `num_threads()` 在 Windows 上每个进程都调一次 `CreateToolhelp32Snapshot`，
而该调用无论如何都要枚举全系统线程表，因此开销随**全系统线程数**放大，与目标进程无关。
换用原生批量接口后，`--once` 输出一帧从约 8.4 秒降到约 0.9 秒，实际刷新周期从设定的 2 秒
（实测劣化到 5.5 秒）回到 2 秒。

- **进程名差异**：原生路径给出的是任务管理器风格的名称（例如 `Memory Compression`，
  而 psutil 给的是 `MemCompression`）。用 `-f` 筛选时请按前者书写。
- **基准与验收**：`python test_tasklight.py` 会断言采集延迟、字段填充率与 CPU% 合法性，
  并覆盖 psutil 回退路径；期望输出"结果: 全部通过"且退出码为 0。
- **平台**：原生采集路径仅 Windows 可用，其它平台会自动回退到 psutil。

## 项目结构

```
tasklight/
├── tasklight.py        主程序（单文件）
├── test_tasklight.py   采集性能与字段保真度回归测试
├── start.bat           启动器（与 tasklight.py 同目录使用）
├── install.bat         生成桌面启动器（自动写入绝对路径）
├── requirements.txt    依赖清单
└── README.md
```

## 许可证

[MIT](LICENSE)

