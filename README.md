# 湖南大学图书馆座位预约助手

非官方 Windows 个人工具，面向使用本人账号的湖南大学图书馆读者。支持三个候选座位、查询预约、更新授权、每日预约与失败补跑。

## 普通用户：使用软件包

1. 将 Windows 压缩包完整解压到固定目录，保留 exe 旁的 `_internal` 文件夹。不要在压缩包中直接运行。
2. 安装并登录桌面微信；自动更新登录需要本机安装 Wireshark 的 tshark 和 Npcap。
3. 打开 `图书馆预约.exe`。首次运行使用空白 Cookie、空座位、无代理配置，自动定时与启动补约默认关闭，不会替你自动提交。
4. 填写自己想要的候选座位，设置预约日期和使用时段。点击“微信程序”可选择 `Weixin.exe`；也会尝试检测已运行的微信及常见安装路径。
5. 点击“更新登录”，按提示进入自己的“湖南大学图书馆 → 服务 → 座位预约”。出现二维码或手机确认时需本人完成。
6. 先用“查询当前预约”确认登录，再按需点击“一键预约（立即提交）”。

软件不包含微信、Wireshark、Npcap 或 Node.js。默认 Python 签名无需 Node；签名失败时的 JavaScript 兜底需要本机能运行 `node`。目前主要适配 `Weixin.exe`，其他版本、主题和显示缩放需要实际验证。

## 设置每天自动预约

在“每天定时预约明天的座位”区域：

- “自动运行时间”：北京时间，例 `22:00`，决定每天什么时候提交。
- “预约开始／预约结束”：第二天使用座位的时段，可选择或输入 `HH:MM`。
- 勾选“启用每日预约”，再点“保存定时设置”。程序会创建或更新 Windows 任务 `ZZLibrary-DailyBooking`，无需手工设置任务计划程序。
- 提前五分钟准备登录，每五分钟检查恢复；登录 Windows 和解锁后也检查补跑。成功或结果不明时不会盲目重发，开始时间已过的旧任务会停止。

关闭界面后，Windows 任务仍可运行。电脑必须开机、登录 Windows；操作微信需要桌面解锁。当前任务没有设置唤醒睡眠或自动开机。移到新电脑后需重新保存定时设置；移动软件目录后也需重新保存，否则任务仍指向旧路径。同一 Windows 账号默认只有一个本工具的计划。

服务器是否允许预约由学校规则决定。提前准备登录不等于提前发送预约；未到开放时间时自动流程会停止本轮尝试。

## 可选：打开程序时自动补约

勾选“打开程序时检查固定预约，缺少时自动补约最近时段”后，下次打开界面会查询当前预约：

- 每日自动运行时间之前检查今天，到点之后检查明天。
- 优先尝试固定时段和候选座位。
- 固定时段被明确拒绝，或开始时间已过时，按 `08:30、10:30、12:30、14:30、16:30、18:30、20:30、22:30` 尝试还未过去的开始时间。
- 同一开始时间按候选座位顺序尝试，结束时间读取服务器允许的最晚时间，首次成功即停。
- 已有覆盖固定时段的预约，或仍有效的成功补约时跳过；当天已有其他预约则提示核实，不自动取消。
- 网络中断等导致提交结果不明时保留状态，后续查询确认前不会盲目重发。

## 配置与隐私

软件数据在 `%LOCALAPPDATA%\ZZLibrary`，包括 `config.json`、授权 Cookie 和运行日志。源码运行默认在项目目录保存数据。`config.example.json` 是可分享的空白示例，不能用个人 `config.json` 替代。

默认直接连接学校网站。如果你的网络需要代理，在自己的 `config.json` 中设置 `proxy`，如 `http://127.0.0.1:端口`，使用你本机的实际端口。请确保该网络能访问学校服务。不要复制别人的 Cookie 或代理路径。

不要分享 `config.json`、抓包、HAR、运行日志、诊断截图或授权缓存。运行日志可能包含预约日期和座位。发布请使用下述发布脚本生成的干净压缩包，不要直接压缩自己的工作目录。

## 开发者：从源码运行

已在 Windows x64、Python 3.13 环境测试；其他版本需自行验证。安装完整 Python（含 tkinter）：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app_entry.py
```

首次启动会从示例创建本机配置。CLI 入口为 `book.py`，详见 `python book.py --help`；查询可用 `python book.py status`。其取消预约命令有真实副作用，请确认目标后使用。

运行离线回归测试（不提交真实预约）：

```powershell
.venv\Scripts\python.exe -m unittest test_auth_refresh test_booking_recovery test_daily_schedule test_quick_book test_startup_booking test_wechat_navigation test_environment_setup
```

构建与生成可分享压缩包：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.venv\Scripts\python.exe build_software.py --output-dir software_release
.venv\Scripts\python.exe package_release.py --software-dir software_release
```

`requirements-tested.txt` 记录本次构建环境的精确版本；普通安装使用 `requirements.txt`。构建始终读取 `config.example.json`，不读取个人 Cookie。发布脚本按明确文件清单复制源码，只包含必要识别图片，收集第三方许可并检查凭据与抓包是否混入。输出目录为上级 `releases/`，含源码包、Windows 软件包和 SHA-256 校验清单。

## 代码结构

| 文件 | 用途 |
|---|---|
| `app_entry.py` / `app_runtime.py` | 软件入口、配置和资源路径 |
| `quick_book.py` | 界面、登录更新、预约流程 |
| `libseat.py` / `book.py` | 接口客户端、命令行 |
| `wechat_navigation.py` / `environment_setup.py` | 微信导航和路径检测 |
| `daily_schedule.py` / `scheduled_book.py` | Windows 任务与补跑 |
| `startup_booking.py` | 启动检查和最近时段补约 |
| `booking_recovery.py` / `signer_node.py` / `fetch_key.py` | 登录及签名恢复 |
| `build_software.py` / `package_release.py` | 构建和干净发布 |
| `test_*.py` | 使用临时文件和模拟接口的测试 |

## 已知限制与许可

微信操作结合系统控件识别和局部图片匹配后点击，图片部分仍受主题、缩放、版本和遮挡影响；不能保证完全无人值守。无需把账号密码交给本工具，但微信手机确认仍需本人操作。预约接口或规则变化时可能需要更新代码。

项目自有代码采用 [MIT 许可证](LICENSE.md)。第三方组件、微信界面局部截图和学校网站内容不因此获得额外授权，说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；Windows 包附带收集到的许可文本。

## 发布到 GitHub

此目录可直接作为仓库根目录，包含源码和空白示例配置，不含个人授权信息。使用 GitHub Desktop 打开此本地仓库，检查更改并提交，再通过 Publish repository 发布为公开仓库。

程序的 zip 和 exe 请放在 GitHub Releases，不要提交到源码仓库。发布前运行测试，再用 `package_release.py` 生成干净的发布包。Issue 或截图中不要附带 Cookie、学号、授权回调链接、抓包及个人运行日志。参与开发见 [CONTRIBUTING.md](CONTRIBUTING.md)。
