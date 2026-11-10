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

### 方式一：下载即用（不用装 Python）

到 [Releases](https://github.com/stone6060/invoice-assistant/releases) 下载
`invoice-assistant-windows-x64.zip`，解压到任意目录，双击 **发票助手.exe**。

- 浏览器会自动打开界面；关掉页面后程序 60 秒自动退出
- 首次启动要扫一遍你的发票目录，张数多时十几秒，控制台窗口会打印进度
- 数据都存在发票目录下的 `.发票助手/` 里，换机器时连同这个文件夹一起拷走即可

### 方式二：从源码运行

| 项目 | 要求 |
| :--- | :--- |
| Python | 3.9 及以上 |
| 依赖 | `openpyxl`、`pymupdf`（两个都是预编译 wheel，不需要编译器） |

````bash
pip install -r requirements.txt
python src/app_entry.py
````

Windows 也可以直接双击仓库根目录的 `启动发票助手.bat`，它会自动找可用的 Python。

> PDF 文本用 **PyMuPDF** 提取，不再需要另外安装 poppler / pdftotext。
> 如果机器上恰好有 `pdftotext`，程序仍会自动探测并作为备选。

### 自己打包 exe

````bash
pip install pyinstaller
python -m PyInstaller --noconfirm 发票助手.spec
````

产物在 `dist/发票助手/` 里，整个文件夹就是可分发的形态（约 91 MB，压 zip 后约 40 MB）。

推一个 `v*` 标签（如 `git tag v1.0.0 && git push origin v1.0.0`），
GitHub Actions 会自动构建 zip 并挂到 Release 上；也可以在 Actions 页面手动触发。

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

- 分类**全手动**，工具不猜；分类与归属存在 `.发票助手/categories.json`，刷新、重启都不丢
- 表格每行有下拉框，也可多选后批量设置
- 「执行归档」会先列出「哪张票 → 哪个文件夹」让你确认，确认后才把文件**移动**到
  `<发票根>/<分类名>/`（只建一层，不套娃）；同名文件自动加 `(2)` 序号，绝不覆盖
- 每次移动都记入 `.发票助手/undo-log.json`；顶栏「撤销归档」可逆序还原
- 移动后分类关系会跟着文件走（内部把 rel_path 改键），不会因为换位置就丢分类

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
详见 [MCP 使用说明](docs/MCP使用说明.md)。

---

## HTTP API

启动后提供本地回环 API，全部需要令牌（除 `/api/health`、`/api/bootstrap`）。

```http
GET    /api/health                        # 探活，无需令牌
GET    /api/bootstrap                     # 页面首次加载取令牌（免令牌，但校验 Host）
GET    /api/summary                       # 汇总（含待确认金额单列）
GET    /api/invoices?q=&status=&limit=    # 列表 / 搜索
GET    /api/invoice/{key}                 # 单张详情（rel_path / filename / 发票号）
GET    /api/invoice/{key}/text            # 票面原文，供 AI 分析
POST   /api/rescan                        # 重新扫描（异步）
GET    /api/scan/status                   # 扫描进度
GET    /api/rules                         # 修复规则
POST   /api/rules                         # 新增规则
DELETE /api/rules/{id}                    # 删除规则
GET    /api/categories                    # 分类与分配
POST   /api/categories                    # 新建分类
POST   /api/categories/assign             # 批量归类 {rel_paths, category}
PATCH  /api/categories/{id}               # 改名 / 折叠
DELETE /api/categories/{id}               # 删除分类
POST   /api/archive                       # 办理归档 {rel_paths, dry_run}
POST   /api/undo                          # 撤销最近一次归档
POST   /api/export                        # 导出 Excel → <发票目录>/导出/
GET    /api/config                        # 读配置（含令牌，需 Header 鉴权）
POST   /api/config/token/reset            # 重置令牌
POST   /api/config/root                   # 更换发票根目录 {root}
POST   /api/reveal                        # 在资源管理器打开 {target: root|data|export}
```

完整清单见 [API 文档](docs/API文档.md)。

令牌在首次启动时随机生成，存于 `.发票助手/config.json`，可在网页「设置」里查看或重置。

---

## 项目结构

```
invoice-assistant/
├── 启动发票助手.bat       双击即用（自动找 Python 并装依赖）
├── requirements.txt
├── src/                  程序代码
│   ├── serve_api.py      入口：起 HTTP 服务 + 页面关闭后自动退出
│   ├── api_service.py    配置与数据访问层，路由分发
│   ├── paths.py          路径与外部工具解析（跨机器可迁移的关键）
│   ├── scan_demo_data.py 扫描引擎：遍历 → 解析 → 去重 → 校验
│   ├── exporter.py       Excel 导出（含公式缓存回填、公式注入防护）
│   ├── categories.py     分类与分配（原子写 + 跨进程文件锁）
│   ├── safezip.py        压缩包护栏（单文件/总量/压缩比上限）
│   ├── mcp_server.py     MCP stdio 服务
│   └── app_entry.py      打包成 exe 时的入口
├── web/                  前端（原生 JS，无构建）
│   ├── index.html
│   ├── app.css
│   └── app.js
├── tests/
│   ├── test_core.py      离线自检（16 项，无需服务、无需发票）
│   ├── test_http_e2e.py  HTTP 端到端（9 项，含安全项）
│   ├── test_all.py       连真实服务的全功能验收（20 项）
│   ├── test_api.py       AI 修复闭环
│   └── test_mcp.py       MCP 协议握手
└── docs/                 详细文档
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
| 扫描件需 OCR | 纯图片 PDF（无文字层）读不出，需先做 OCR；本工具不含 OCR |
| 分发形态是文件夹 | 打包用 onedir 而非单文件：`--onefile` 每次启动都要解压到 TEMP，在限制了临时目录权限的机器上会直接失败 |

遇到问题或有改进想法，欢迎提 Issue。

---

## 开发

```bash
# 离线自检（不需要服务、不需要发票）
python tests/test_core.py          # 16 项
python tests/test_http_e2e.py      # 9 项，自己起服务

# 全功能验收（需先启动服务）
python src/serve_api.py &
python tests/test_all.py           # 20 项
```

启动服务时如果不在仓库根目录，用 `--root` 显式指定发票目录。

测试覆盖：鉴权、汇总、搜索、详情、原文提取（PDF 与铁路票 ZIP）、规则增删、异步扫描、Excel 导出与公式缓存、MCP 路径校验。

---

## 许可证

[MIT](LICENSE)
