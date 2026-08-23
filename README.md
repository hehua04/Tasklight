# TaskLight 轻量级 Windows 任务管理器

一个单文件、低开销的进程查看器，用来替代"打开慢、偶尔卡住"的 Windows 任务管理器。
可按 CPU / 内存（工作集）/ 已提交内存 等排序浏览全部进程，并支持筛选、查看详情与结束进程。

```
TaskLight v0.1.0 │ 进程 352 │ CPU 22.6% │ 内存 9.9GB/15.2GB(65%) │ 提交 22.2GB/30.0GB
    PID 名称                          CPU%   内存(WS)       提交  线程
  26668 msedge.exe                     2.6     510.6MB   473.7MB    30
  28948 msedge.exe                     0.3     493.1MB     1.1GB   123
   6100 MsMpEng.exe                    1.8     305.8MB         -    73
  ...
[↑↓]选择 [Enter]详情 [c/m/p/i/n]排序 [Tab]轮换 [a]升降序 [f]筛选 [k]结束 [空格]刷新 [+/-]间隔 [q]退出
```

## 功能特性

- **轻量**：单个 `.py` 文件 + psutil，秒开，常驻内存仅十几 MB
- **列信息**：PID、名称、CPU%（相对整机归一化）、内存（工作集/物理占用）、已提交（私有）内存、线程数
- **顶栏总览**：系统 CPU 占用、物理内存用量/占比、提交内存用量/上限、进程总数
- **排序**：任意列排序，`Tab` 或 `←→` 轮换排序列，`a` 切换升降序
- **筛选**：按进程名实时过滤，支持 `*` `?` 通配符
- **详情视图**：命令行、可执行文件路径、父进程、创建时间、用户名、句柄数、IO 字节数等
- **结束进程**：选中后一键终止（需确认），权限不足会给出提示
- **脚本友好**：`--once` 模式输出一帧即退出，方便配合其他工具使用

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

- **已提交内存**：读取进程的私有提交字节（`PrivateUsage`），与任务管理器"详细信息"页中的"提交大小"一致；顶栏的提交总量/上限来自系统全局统计。
- **权限**：普通权限下即可查看绝大多数进程；少数受保护的系统进程（如 MemCompression、MsMpEng、dwm 等）需以**管理员身份**运行才能读到提交内存和详情，否则对应列显示 `-`。
- **首次显示**：启动后第一帧的 CPU 列为预热数据，第二个刷新周期起即为准确值。

## 项目结构

```
tasklight/
├── tasklight.py        主程序（单文件）
├── start.bat           启动器（与 tasklight.py 同目录使用）
├── install.bat         生成桌面启动器（自动写入绝对路径）
├── requirements.txt    依赖清单
└── README.md
```

## 许可证

[MIT](LICENSE)

