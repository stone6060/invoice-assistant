# 发票助手 · API 接口文档

> 设计理念：**应用本体保持精简，能力全部通过 API 暴露**。
> 修复逻辑不写死在程序里，由外部（AI / 脚本 / 其他工具）通过 API 驱动。

---

## 一、启动

```bash
python src/serve_api.py
```

启动后输出：

```
发票助手 已启动
网页     http://127.0.0.1:17802/index.html
管理API  http://127.0.0.1:17802/api/health
令牌     <你的令牌>
```

- 默认端口 `17800`，被占用时自动顺延
- 令牌首次启动自动生成，存 `.发票助手/config.json`
- 网页里「设置」可查看/复制/重置令牌

---

## 二、鉴权

只认请求头：

```http
X-Api-Token: <你的令牌>
```

**不再支持 `?token=` 查询参数** —— URL 会进浏览器历史、代理日志和 Referer，
令牌一旦这么带出去就等于泄露。

`/api/health` 与 `/api/bootstrap` 免令牌：前者探活，后者只返回「令牌 + 端口」
供页面首次加载，且位于 `Host` 校验之后（仅接受 `127.0.0.1` / `localhost` / `[::1]`，
防 DNS rebinding）。

---

## 三、接口清单

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/bootstrap` | 取令牌、端口与 MCP 配置（免令牌，但校验 `Host`） |
| GET | `/api/summary` | 汇总：文件数 / 计账数 / 总金额 / 重复 / 待确认 / 待确认金额 |
| GET | `/api/invoices` | 发票列表，支持 `status` / `q` / `limit` |
| GET | `/api/invoice/{key}` | 单张详情，key 可为 rel_path / filename / invoice_no |
| GET | `/api/invoice/{key}/text` | **原文文本**，供 AI 分析排版问题 |
| GET | `/api/rules` | 修复规则列表 |
| POST | `/api/rules` | 新增修复规则（字段白名单校验） |
| DELETE | `/api/rules/{id}` | 删除规则 |
| GET | `/api/categories` | 分类列表 + 分配关系 + 各分类张数与金额 |
| POST | `/api/categories` | 新建分类 `{name, color?}` |
| POST | `/api/categories/assign` | 批量归类 `{rel_paths:[], category:<id 或 null>}` |
| PATCH | `/api/categories/{id}` | 改名 `{name}` 或折叠 `{collapsed}` |
| DELETE | `/api/categories/{id}` | 删除分类（其下的票回到「待归类」） |
| POST | `/api/archive` | 归档：把已分类的票移动到 `<发票根>/<分类名>/`，`{rel_paths, dry_run}` |
| POST | `/api/undo` | 撤销最近一次归档（逆序移回原位） |
| POST | `/api/export` | 导出 Excel `{name?}` → `<发票目录>/导出/` |
| POST | `/api/rescan` | 重新扫描（异步，立即返回） |
| GET | `/api/scan/status` | 扫描进度 `{running, done, total, error?}` |
| GET | `/api/config` | 读配置（含令牌与 MCP 路径，需 Header 鉴权） |
| POST | `/api/config/token/reset` | 重置令牌，返回新令牌 |

分类接口的 `id` 形如 `cat_12345678_ab12`；`category` 传 `null` 表示移出分类。
删除分类不会删票，只会把该分类下的票置为「待归类」。

---

## 四、AI 修复闭环（核心用法）

以「胶带票金额读不出」为例，AI 的完整动作序列：

### 1. 找异常票

```http
GET /api/invoices?status=unverified
X-Api-Token: <token>
```

```json
{
  "total": 1,
  "items": [{
    "filename": "dzfp_26952000000000000001_示例科技有限公司_20260101120000.pdf",
    "amount": null,
    "status": "unverified",
    "unverified_reason": "amount_missing"
  }]
}
```

### 2. 取原文分析

```http
GET /api/invoice/{rel_path}/text
X-Api-Token: <token>
```

```json
{
  "filename": "...",
  "text": "...价税合计（大写）\n壹圆伍角 整\n.50\n（小写）¥1\n...",
  "hint": { "amount": null, "amount_upper": 1.5, "status": "unverified" }
}
```

AI 从原文立刻能看出：**「¥1」和「.50」被排版拆成了两行**，而大写是「壹圆伍角整」= 1.50。

### 3. 写入修复规则

```http
POST /api/rules
Content-Type: application/json
X-Api-Token: <token>

{
  "match": { "filename_contains": "4282374901" },
  "field": "amount",
  "value": 1.5,
  "note": "PDF 把「¥1」与「.50」拆成两行，按大写「壹圆伍角整」补全"
}
```

### 4. 验证

```http
GET /api/invoices?q=4282374901
```

```json
{ "items": [{ "amount": 1.5, "amount_upper": 1.5, "status": "normal" }] }
```

**全程不改源码、不重启程序**，规则持久化在 `.发票助手/fix_rules.json`，重启后依然生效。

---

## 五、修复规则格式

```json
{
  "id": "r_2a064990",
  "match": {
    "filename_contains": "4282374901",   // 三选一
    "invoice_no": "26952000000000000001",
    "rel_path": "111/xxx.pdf"
  },
  "field": "amount",                      // 要覆盖的字段
  "value": 1.5,
  "note": "修复说明",
  "created_at": "2026-10-20T19:30:00"
}
```

支持覆盖的字段：`amount`、`amount_upper`、`invoice_no`、`seller`、`buyer`、`issue_date`、`item`、`tax`、`category`、`status`。

---

## 六、MCP 接入

`/api/mcp` 预留为标准 MCP 端点（当前为占位，后续可对接 Claude Desktop / Cursor / Cline）。

配置示例（Claude Desktop `claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "invoice-assistant": {
      "url": "http://127.0.0.1:17802/api/mcp",
      "headers": { "X-Api-Token": "<token>" }
    }
  }
}
```

---

## 七、安全模型

| 项 | 默认 | 说明 |
| :--- | :--- | :--- |
| 监听地址 | `127.0.0.1` | 仅本机可访问 |
| 令牌 | 自动生成 | 32 位十六进制，可重置 |
| 局域网访问 | 关闭 | 开启后需自行保管令牌 |
| CORS | 关闭 | 仅网页调不到本机服务时开启 |
| 功能开关 | 管理API/MCP 开 | 可独立关闭 |

> 注意：`/api/config?reveal=1` 会回显令牌，仅建议在回环地址下使用。

---

## 八、为什么这样设计

你最初的问题是「应用会不会太臃肿」。这套设计的取舍：

| 做法 | 结果 |
| :--- | :--- |
| 把 AI 塞进程序 | 程序依赖模型可用性，体积变大，改规则要发版 |
| **把能力开放成 API** | **程序只做稳定的原子操作；AI 在外面按需调用；修复记录成规则文件，可审计、可回滚** |

所以遇到「这张票读不准」时，不需要我改代码、重启、重扫，而是：

```
AI 查异常 → 取原文 → 推断规则 → POST /api/rules → 验证
```

程序本身一行没动，问题已经修好了。
