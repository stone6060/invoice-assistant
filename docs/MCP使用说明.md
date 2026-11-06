# 发票助手 · MCP Server 使用说明

> MCP（Model Context Protocol）是让 AI 客户端（Claude Desktop / Cursor / Cline）
> 直接调用外部能力的标准协议。发票助手实现了完整的 MCP 服务端。

---

## 一、为什么用 stdio 而不是 HTTP

| 方式 | 端口 | 令牌 | 安全面 | 适用 |
| :--- | :--- | :--- | :--- | :--- |
| **stdio（本方案）** | 不需要 | 不需要 | 进程内通信，零网络暴露 | Claude Desktop / Cursor / Cline |
| HTTP + SSE | 需要 | 需要 | 要开端口、管令牌、防 CORS | 远程/多客户端 |

MCP 官方客户端默认就用 stdio——**客户端自己拉起进程，不用端口、不用令牌、不用 CORS**。
这正是你想要的「不臃肿」。

---

## 二、接入配置

启动一次 `src/serve_api.py`，网页右上角齿轮 →「设置」→ 复制 MCP 配置即可（路径会自动填成真实值）。

### Claude Desktop

配置文件位置：
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`
- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "invoice-assistant": {
      "command": "C:\\Users\\<你>\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\dependencies\\python\\python.exe",
      "args": [
        "<发票助手目录>\\src\\mcp_server.py"
      ]
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json`，格式同上。

### Cline（VS Code 插件）

设置 → MCP Servers → 添加，格式同上。

> 填好后重启客户端，输入框旁边会出现工具图标，说明连接成功。

---

## 三、可用工具（10 个）

| 工具名 | 作用 | 必填参数 |
| :--- | :--- | :--- |
| `invoice_summary` | 汇总：文件数、计账张数、总金额、重复、待确认 | — |
| `invoice_list` | 查发票列表，可按状态/关键字筛选 | — |
| `invoice_get` | 单张发票完整字段 | `key` |
| `invoice_raw_text` | 取 PDF 原文（分析识别失败原因） | `key` |
| `invoice_rescan` | 重新扫描目录 | — |
| `invoice_rules_list` | 查看修复规则 | — |
| `invoice_rule_add` | 新增修复规则 | `match` `field` `value` |
| `invoice_rule_delete` | 删除修复规则 | `id` |
| `invoice_set_category` | 把一张票归入分类（分类名必须由用户给出） | `key` `category` |
| `invoice_export` | 导出 Excel（汇总 / 分类小计 / 异常清单） | — |

`key` 可以是相对路径、文件名或发票号。

---

## 四、在对话里怎么用

接上之后，你可以直接说：

> 「帮我看看有没有金额没读出来的发票」

AI 会自动调用 `invoice_list(status="unverified")`。

> 「这张票为什么没有价格」

AI 会调用 `invoice_raw_text(key=...)` 拿到原文，分析排版问题，然后可能调用 `invoice_rule_add` 写修复规则，再 `invoice_rescan` 验证。

> 「总共多少钱」

调用 `invoice_summary`。

---

## 五、修复闭包的完整链路

```
用户: 这张票金额不对
  ↓
AI: invoice_raw_text(key)        → 看到「（小写）¥1」和「.50」被拆成两行
  ↓
AI: 发现大写是「壹圆伍角整」= 1.50
  ↓
AI: invoice_rule_add(match={filename_contains:"4282374901"}, field="amount", value=1.5)
  ↓
AI: invoice_rescan()
  ↓
AI: invoice_get(key)              → 确认 amount=1.5
```

**全程不改源码、不重启程序**，规则持久化在 `.发票助手/fix_rules.json`。

---

## 六、验证

```bash
# 协议自测（模拟 Claude Desktop 握手）
python tests/test_mcp.py
```

预期输出：

```
① initialize 握手
   server = invoice-assistant v0.3.0
   protocol = 2024-11-05

② tools/list 工具清单
   共 10 个工具：...

③ invoice_summary
   共 80 个文件，计账 76 张，总金额 ¥8764.86；重复 1 张，待确认 0 张。
```

---

## 七、故障排查

| 现象 | 原因 | 解决 |
| :--- | :--- | :--- |
| 客户端看不到工具 | 路径写错或 Python 不可执行 | 用设置页复制的配置，别手打 |
| 工具调用失败 | 发票目录没配置 | 先启动一次 `src/serve_api.py` |
| 中文乱码 | Windows 默认 GBK | 已强制 UTF-8，`mcp_server.py` 内处理 |
| 日志在哪 | — | `.发票助手/mcp.log` |

---

## 八、架构位置

```
发票助手/
├── src/
│   ├── mcp_server.py  ← MCP 服务端（stdio，AI 客户端调用）
│   └── serve_api.py   ← 本地服务（网页 + 本机 API）
├── api_service.py     ← 共享能力层（配置 / 规则 / 数据）
├── scan_demo_data.py  ← 扫描解析引擎
└── web/               ← 网页界面
```

`mcp_server.py` 和 `serve_api.py` 都只依赖 `api_service.py`，能力复用、互不耦合。
