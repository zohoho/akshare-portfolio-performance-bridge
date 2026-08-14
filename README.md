# AkShare–Portfolio Performance 桥接器

简体中文 | [English](README_EN.md)

一款面向 Windows 的本地桌面桥接器，为
[Portfolio Performance](https://www.portfolio-performance.info/) 提供中国内地、香港和美国市场的 JSON/CSV 报价接口。程序只监听
`127.0.0.1:18765`，不维护持仓，也不会把 Portfolio Performance 数据上传到网络。

当前版本：`2.0.3`

## 主要功能

- 中国开放式基金、场内 ETF 和 A 股历史报价及最新价。
- 港股、美股和美基日线报价。
- 香港基金内部代码，以及已接入基金公司的 ISIN 官方净值。
- Windows 登录后自动启动后台服务；关闭管理窗口后服务继续运行。
- GUI 中测试报价、复制 Portfolio Performance 配置、暂停或结束服务。
- 启动时及每 24 小时检查 AkShare 稳定版；只有用户确认后才安装，并支持回滚。

## 界面截图

仓库公开前会在 [`docs/screenshots`](docs/screenshots/) 补充不含账户、持仓和本机路径的界面截图。

## 安装

从 GitHub Releases 下载 `AkSharePPBridge-Setup-2.0.3.exe`。安装程序按当前用户安装，无需管理员权限；2.0.3 沿用既有 AppId 和安装目录，可以覆盖旧版本且保留配置、缓存、日志与 AkShare 运行环境。

- 默认安装目录：`%LOCALAPPDATA%\Programs\AkSharePPBridge`
- 默认数据目录：`%LOCALAPPDATA%\AkSharePPBridge`
- 本地服务地址：`http://127.0.0.1:18765`
- 健康检查：`http://127.0.0.1:18765/health`

安装后服务立即启动，并通过当前用户的 Windows 计划任务在登录时自动启动。关闭 GUI 不会停止服务；“暂停服务”保留登录自启，“结束后台服务”会同时停止服务并关闭登录自启。

## Portfolio Performance 配置

历史报价提供方选择 `JSON`：

```text
日期路径：$.prices[*].date
收盘路径：$.prices[*].close
最高路径：$.prices[*].high       # 股票可选
最低路径：$.prices[*].low        # 股票可选
成交量路径：$.prices[*].volume   # 股票可选
日期格式：留空
```

基金在“最新报价”中可选择“与历史报价相同”。使用 `{TICKER}` 占位符后，新增同类资产只需修改证券代码，无需重新配置桥接服务。

### 中国基金与 ETF

```text
开放式基金历史：http://127.0.0.1:18765/fund/{TICKER}.json?kind=open
开放式基金最新：http://127.0.0.1:18765/fund/{TICKER}/latest.json?kind=open
场内 ETF 历史：http://127.0.0.1:18765/fund/{TICKER}.json?kind=etf
场内 ETF 最新：http://127.0.0.1:18765/fund/{TICKER}/latest.json?kind=etf
```

可用 `000305` 测试开放式基金。

### A 股

支持 `300308.SZ`、`600519.SH`、`600519.SS`、北交所后缀及无后缀六位代码。历史行情优先腾讯、东方财富备用；最新价使用腾讯全市场快照并缓存 30 秒。

```text
历史：http://127.0.0.1:18765/stock/{TICKER}.json
最新：http://127.0.0.1:18765/stock/{TICKER}/latest.json
```

### 港股与美股

港股可写 `0700` 或 `0700.HK`；美股使用 `MSFT`、`AAPL` 等代码。

```text
港股历史：http://127.0.0.1:18765/stock/hk/{TICKER}.json
港股最新：http://127.0.0.1:18765/stock/hk/{TICKER}/latest.json
美股历史：http://127.0.0.1:18765/stock/us/{TICKER}.json
美股最新：http://127.0.0.1:18765/stock/us/{TICKER}/latest.json
```

### 港基与美基

港基可输入天天基金香港基金内部代码。已接入的基金公司还可直接输入 ISIN；例如 `HK0000294535` 从泰康资产（香港）官网读取并严格匹配 `Class A-HKD-DIST`、HKD 份额。

```text
港基历史：http://127.0.0.1:18765/fund/hk/{TICKER}.json
港基最新：http://127.0.0.1:18765/fund/hk/{TICKER}/latest.json
美基历史：http://127.0.0.1:18765/fund/us/{TICKER}.json
美基最新：http://127.0.0.1:18765/fund/us/{TICKER}/latest.json
```

所有历史 JSON 接口都有对应 `.csv` 地址。可附加 `?refresh=1` 强制刷新，或使用 `?start=2025-01-01&end=2025-12-31` 限制历史日期。

## 从源码运行与测试

需要 Windows 和 Python 3.12：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:AKSHARE_PP_DATA_DIR = "$PWD\.local-data"
.\.venv\Scripts\python.exe bridge.py
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试不会修改正式 `%LOCALAPPDATA%\AkSharePPBridge` 数据目录。

## 构建 Windows 安装包

需要 Python 3.12、PyInstaller 和 Inno Setup 6：

```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\build_app.ps1 -BuildPython .\.venv-build\Scripts\python.exe
.\build_installer.ps1
```

如 Inno Setup 不在标准位置，可传入：

```powershell
.\build_installer.ps1 -IsccPath "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

安装包输出到 `dist/`。构建脚本也支持显式指定 `-RuntimePython` 和 `-RuntimeSitePackages`，便于从独立、干净的运行环境生成安装包。

## 数据来源与安全

桥接器会调用 AkShare、Yahoo Finance、腾讯、东方财富以及已接入基金公司的公开接口。上游失败但本地存在旧缓存时，服务返回旧数据并设置 `stale: true`。

程序只监听回环地址，控制接口使用本地随机令牌。报价仅用于个人投资记账，不应作为交易依据。请自行确认数据源条款及所在地区的适用规则。

## 版本 2.0.3

- 支持中国基金、ETF、A 股、港股、美股、港基和美基。
- Yahoo 历史行情输出完整日线、最高、最低和成交量。
- 新增香港基金 ISIN 识别；`HK0000294535` 使用泰康资产（香港）官方净值。
- 提供填写说明、服务暂停/结束、登录自启和 AkShare 安全更新回滚。

## 许可证与声明

本项目按 [GNU General Public License v3.0](LICENSE) 发布，不提供任何担保。第三方组件和数据源保留各自的许可证及权利，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

Portfolio Performance 与 AkShare 是独立项目。本桥接器是非官方社区工具，与上述项目不存在赞助、认可或隶属关系。
