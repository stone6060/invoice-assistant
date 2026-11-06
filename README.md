# 发票助手 · Invoice Assistant

> 把一堆散落的中文电子发票，自动读出来、算清楚、归好类，再导出成 Excel。
> 纯本地运行，数据不出机器。

## 它解决什么问题

每月报销或课题结题时，发票总是散在各种下载文件夹里：`_26952000000000000000.pdf` 这种名字看不出是什么，金额要一张张点开看，还得手动加总。

这个工具做三件事：

1. **读** —— 解析 PDF / OFD（含铁路电子客票）/ ZIP，提取发票号、金额、销售方、开票日期、商品名称
2. **验** —— 用大写金额交叉校验小写金额，对不上就标「待确认」并单列金额，避免错账
3. **整** —— 按发票号去重（重复票、容器包、解析失败都不计账），手动分类归档，一键导出 Excel

---

## 快速开始

### 环境要求

| 项目 | 要求 |
| :--- | :--- |
| Python | 3.9 及以上 |
| 依赖 | `openpyxl`（其余全为标准库） |
| pdftotext | 需要 **poppler 版**，见下方说明 |

```bash
pip install -r requirements.txt
```

### 关于 pdftotext（重要）

PDF 文本提取依赖 `pdftotext`。**必须是 poppler 版本**：

- ✅ poppler（版本号形如 `pdftotext version 24.04.0`）—— 支持中文
- ❌ xpdf（版本号含 `www.xpdfreader.com`）—— 会**静默吞掉中文**，解析结果几乎为空

安装方式：

```bash
# Windows：下载 poppler for Windows，解压后把 bin 目录加入 PATH
#   或安装 MiKTeX（自带 pdftotext）
# macOS
brew install poppler
# Ubuntu / Debian
sudo apt install poppler-utils
```

程序启动时会自动探测并**实测**候选 `pdftotext`，遇到 xpdf 构建会自动跳过。
如果全部不可用，网页顶部会给出明确提示，而不是静默失败。

### 启动

```bash
# Windows
双击「启动发票助手.bat」

# 任意平台
python serve_api.py
```

浏览器会自动打开 `http://127.0.0.1:17800/index.html`（端口被占用时自动顺延）。
**关闭页面约 60 秒后服务自动退出**，不留后台进程。

### 指定发票目录

程序默认把**自身所在目录的上一级**当作发票根目录，并自动探测该处是否真有发票文件。
可以显式指定：

```bash
# 方式一：环境变量（优先级最高）
set INVOICE_ROOT=D:\我的发票      # Windows
export INVOICE_ROOT=~/invoices    # macOS / Linux

# 方式二：网页「设置」面板里修改并保存
```

推荐的目录布局：

```
我的发票/                  ← 发票根目录（含 .pdf）
├── 发票助手/              ← 本程序
│   ├── serve_api.py
│   └── web/
├── .发票助手/             ← 运行期数据（自动生成，勿提交）
│   ├── config.json
│   └── fix_rules.json
├── 一号发票.pdf
└── 111/                   ← 子目录也会被递归扫描
```

---

## 功能说明

### 扫描与去重

- 递归扫描根目录下所有子文件夹
- 自动展开 `.zip`，识别其中的 OFD / XBRL（铁路电子客票格式）
- **按发票号去重**：同一张票的压缩包和已解压文件只计一次
- 首次运行**只扫描、不改动任何文件**

### 金额校验

每张票同时提取：

- 小写金额：`（小写）¥123.45`
- 大写金额：`壹佰贰拾叁圆肆角伍分`

两者换算后必须一致，否则该行标记为**异常**并在导出时单独列出。
这条校验抓出过真实错误——大写里「零」的位置写错时，正则容易只匹配到半个金额。

### 分类与归档

- 分类**全手动**，工具不猜
- 表格每行有下拉框，也可多选后批量设置
- 「执行归档」会把文件**移动到**对应分类文件夹（只建一层，不套娃）
- 每次移动都记入 `undo-log.json`，可撤销

### 导出 Excel

三个工作表：

| 工作表 | 内容 |
| :--- | :--- |
| 发票汇总 | 全部发票明细，含校验状态 |
| 分类小计 | 按分类汇总张数与金额 |
| 异常清单 | 金额校验未通过、缺文件等 |

导出的公式会同时写入**缓存值**，保证 WPS、Excel 预览、各类在线预览器都能直接看到总金额
（只用 openpyxl 写公式不带缓存值时，这些工具会显示空白）。

### MCP 接口

可作为 MCP Server 接入 Claude Desktop / Cursor / Cline，让 AI 直接查你的发票数据。
详见 [MCP使用说明.md](MCP使用说明.md)。

---

## HTTP API

启动后提供本地回环 API，全部需要令牌（除 `/api/health`、`/api/bootstrap`）。

```http
GET  /api/health                         # 探活，无需令牌
GET  /api/bootstrap                      # 页面首次加载取令牌（免令牌，但校验 Host）
GET  /api/summary                        # 汇总统计（含待确认金额单列）
GET  /api/invoices?q=关键字&limit=500     # 列表 / 搜索
GET  /api/invoice?key=<key>              # 单张票详情
POST /api/rescan                         # 重新扫描（异步）
GET  /api/scan/status                    # 扫描进度
GET  /api/categories                     # 分类与分配
POST /api/categories/assign              # 批量归类 {rel_paths, category}
POST /api/export                         # 导出 Excel → <发票目录>/导出/
```

完整清单见 [API文档.md](API文档.md)。

令牌在首次启动时随机生成，存于 `.发票助手/config.json`，可在网页「设置」里查看或重置。

---

## 项目结构

```
invoice-assistant/
├── serve_api.py          入口：起 HTTP 服务 + 页面关闭后自动退出
├── api_service.py        配置与数据访问层，路由分发
├── paths.py              路径与外部工具解析（跨机器可迁移的关键）
├── scan_demo_data.py     扫描引擎：遍历 → 解析 → 去重 → 校验 → 输出数据
├── exporter.py           Excel 导出（含公式缓存回填、公式注入防护）
├── categories.py         分类与分配（原子写 + 跨进程文件锁）
├── safezip.py            压缩包护栏（单文件/总量/压缩比上限）
├── mcp_server.py         MCP stdio 服务
├── app_entry.py          打包成 exe 时的入口
├── web/
│   ├── index.html
│   ├── app.css
│   └── app.js
├── test_core.py          离线自检（16 项，无需服务、无需发票）
├── test_http_e2e.py      起服务的 HTTP 端到端（9 项，含安全项）
└── test_all.py           连真实服务的全功能验收
```

---

## 数据安全

- 所有解析在**本地**完成，除你主动配置的 AI 兜底外不联网
- 服务只监听 `127.0.0.1`，并校验 `Host` 头，挡掉 DNS rebinding
- API 需要令牌，令牌每次安装随机生成
- 仓库**不包含任何发票原件**（`.gitignore` 已排除所有票据格式）

---

## 已知限制

| 限制 | 说明 |
| :--- | :--- |
| 容器包不展开计账 | 压缩包整体标为「容器包」不计账，不会按包内文件逐张核对；若你把发票打包后原件也删了，合计会少算 |
| 一张 PDF 多张票 | 只取第一张的金额 |
| 仅中文发票 | 针对中国电子发票版式设计，其他国家票据不适用 |
| pdftotext 必需 | 纯图片扫描件（无文字层）读不出，需 OCR |
| 单文件 exe 不稳定 | 部分 Windows 环境下 PyInstaller `--onefile` 会因 TEMP 权限失败，建议用 `.bat` 启动 |

遇到问题或有改进想法，欢迎提 Issue。

---

## 开发

```bash
# 跑测试（需先启动服务）
python serve_api.py &
python test_all.py
```

测试覆盖：鉴权、汇总、搜索、详情、原文提取（PDF 与铁路票 ZIP）、规则增删、异步扫描、Excel 导出与公式缓存、MCP 路径校验。

---

## 许可证

[MIT](LICENSE)
