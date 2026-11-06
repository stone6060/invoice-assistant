#!/usr/bin/env python
"""发票助手 · MCP Server（stdio 传输）

MCP（Model Context Protocol）服务端，供 Claude Desktop / Cursor / Cline
等 AI 客户端直接接入。

为什么用 stdio：
  · 客户端自己拉起进程，不占端口、不用令牌、不用 CORS
  · 最少的攻击面，也最符合「不要臃肿」的要求
  · 直接复用 api_service 的能力，零额外依赖

接入方式（Claude Desktop 的 claude_desktop_config.json）：

  {
    "mcpServers": {
      "invoice-assistant": {
        "command": "<python路径>",
        "args": ["<本文件绝对路径>"]
      }
    }
  }

协议：JSON-RPC 2.0，消息按行分隔（newline-delimited）。
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api_service as api

SERVER_NAME = 'invoice-assistant'
SERVER_VERSION = '0.3.0'
PROTOCOL_VERSION = '2024-11-05'

LOG_PATH = os.path.join(api.DATA_DIR, 'mcp.log')


def log(msg):
    """日志写到文件，绝不能污染 stdout（stdout 是协议通道）。"""
    try:
        api.ensure_dirs()
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            import datetime
            ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            f.write(f'[{ts}] {msg}\n')
    except Exception:
        pass


# ---------------------------------------------------------------- 工具实现
def tool_summary(cfg, args):
    s = api.api_summary(cfg)
    return (
        f"共 {s['files']} 个文件，计账 {s['counted']} 张，"
        f"总金额 ¥{s['total']:.2f}；"
        f"重复 {s['duplicates']} 张，待确认 {s['unverified']} 张。"
    )


def tool_list(cfg, args):
    q = {}
    if args.get('status'):
        q['status'] = args['status']
    if args.get('q'):
        q['q'] = args['q']
    q['limit'] = str(args.get('limit', 50))

    r = api.api_list(cfg, q)
    if not r['items']:
        return f"没有匹配的发票（共 {r['total']} 条）。"

    lines = [f"共 {r['total']} 条，显示前 {r['returned']} 条：", '']
    for i, it in enumerate(r['items'], 1):
        amt = f"¥{it['amount']:.2f}" if it.get('amount') is not None else '—'
        lines.append(
            f"{i}. {it['filename']}\n"
            f"   金额 {amt} | 发票号 {it.get('invoice_no') or '—'} | "
            f"{it.get('seller') or '—'} | {it.get('issue_date') or '—'} | "
            f"状态 {it.get('status')}"
        )
    return '\n'.join(lines)


def tool_get(cfg, args):
    key = args.get('key') or ''
    it = api.api_get(cfg, key)
    if not it:
        return f"未找到发票：{key}"
    return json.dumps(it, ensure_ascii=False, indent=2)


def tool_raw_text(cfg, args):
    key = args.get('key') or ''
    r = api.api_raw_text(cfg, key)
    if r.get('error'):
        return f"读取失败：{r['error']}（{r.get('path', '')}）"
    return (
        f"文件：{r['filename']}\n"
        f"当前解析：金额={r.get('hint', {}).get('amount')} "
        f"大写换算={r.get('hint', {}).get('amount_upper')} "
        f"状态={r.get('hint', {}).get('status')}\n"
        f"{'-' * 50}\n"
        f"{r['text']}"
    )


def tool_rescan(cfg, args):
    """重新扫描目录，刷新数据。"""
    import importlib
    import scan_demo_data

    # 让扫描器使用当前配置里的根目录
    root = cfg['scan'].get('root')
    if root:
        scan_demo_data.ROOT = root
        scan_demo_data.DIR111 = os.path.join(root, '111')

    importlib.reload(scan_demo_data) if False else None
    if root:
        scan_demo_data.ROOT = root

    scan_demo_data.main()

    s = api.api_summary(cfg)
    return (
        f"扫描完成：{s['files']} 个文件，计账 {s['counted']} 张，"
        f"总金额 ¥{s['total']:.2f}，待确认 {s['unverified']} 张。"
    )


def tool_rules_list(cfg, args):
    rules = api.load_rules()
    if not rules:
        return '当前没有修复规则。'
    lines = [f'共 {len(rules)} 条修复规则：', '']
    for r in rules:
        m = r.get('match', {})
        cond = ', '.join(f'{k}={v}' for k, v in m.items())
        lines.append(f"· {r['id']}  匹配[{cond}]  {r['field']}={r['value']}\n  {r.get('note', '')}")
    return '\n'.join(lines)


def tool_rules_add(cfg, args):
    rule = {
        'match': args.get('match') or {},
        'field': args.get('field'),
        'value': args.get('value'),
        'note': args.get('note', ''),
    }
    if not rule['field']:
        return '失败：field 必填（如 amount）'
    if not rule['match']:
        return '失败：match 必填（如 {"filename_contains": "xxx"}）'
    r = api.add_rule(rule)
    return f"已新增规则 {r['id']}：{r['field']}={r['value']}"


def tool_rules_delete(cfg, args):
    rid = args.get('id') or ''
    n = api.delete_rule(rid)
    return f'已删除 {n} 条规则。' if n else f'未找到规则 {rid}'


def tool_set_category(cfg, args):
    """把一张发票归入分类（分类不存在则新建），分类写入 .发票助手/categories.json。"""
    key = args.get('key') or ''
    cat = (args.get('category') or '').strip()
    it = api.find_item(key)
    if not it:
        return f'未找到发票：{key}'

    rel = it.get('rel_path') or it.get('filename')

    if not cat or cat in ('未分类', 'none', 'None', '-'):
        api.CATEGORY_STORE.assign([rel], None)
        return f"已把「{it['filename']}」移出分类，现在是未分类。"

    snap = api.CATEGORY_STORE.snapshot()
    target = next((c for c in snap['categories'] if c['name'] == cat), None)
    created = False
    if target is None:
        try:
            target = api.CATEGORY_STORE.create(cat)
            created = True
        except ValueError as e:
            return f'分类创建失败：{e}'

    api.CATEGORY_STORE.assign([rel], target['id'])
    return (
        f"已把「{it['filename']}」归入分类「{target['name']}」"
        + ('（该分类为新建）' if created else '')
        + f"。金额 {it.get('amount')}。"
    )


def tool_export(cfg, args):
    """导出 Excel。"""
    r = api.api_export(cfg, {
        'status': args.get('status'),
        'q': args.get('q'),
        'filename': args.get('filename'),
        'label': args.get('label'),
    })
    if r.get('error'):
        return f"导出失败：{r['error']}"
    return (
        f"已导出到：{r['path']}\n"
        f"共 {r['files']} 个文件，计账 {r['counted']} 张，"
        f"总金额 ¥{r['total']:.2f}，分类 {r['groups']} 组，异常 {r['abnormal']} 条。\n"
        f"（含 3 个工作表：发票汇总 / 分类小计 / 异常清单）"
    )


# ---------------------------------------------------------------- 工具定义
TOOLS = [
    {
        'name': 'invoice_summary',
        'description': '获取发票汇总统计：文件数、计账张数、总金额、重复数、待确认数。',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'handler': tool_summary,
    },
    {
        'name': 'invoice_list',
        'description': '查询发票列表，可按状态筛选或关键字搜索。status 可选 normal/dup/unverified/container。',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string', 'description': '按状态筛选，如 unverified'},
                'q': {'type': 'string', 'description': '关键字（文件名/销售方/发票号/项目）'},
                'limit': {'type': 'integer', 'description': '最多返回条数，默认 50'},
            },
            'additionalProperties': False,
        },
        'handler': tool_list,
    },
    {
        'name': 'invoice_get',
        'description': '获取单张发票的完整字段。key 可以是 rel_path、文件名或发票号。',
        'inputSchema': {
            'type': 'object',
            'properties': {'key': {'type': 'string', 'description': '发票标识'}},
            'required': ['key'],
            'additionalProperties': False,
        },
        'handler': tool_get,
    },
    {
        'name': 'invoice_raw_text',
        'description': '获取发票 PDF 的原始文本，用于分析金额/字段识别失败的原因。',
        'inputSchema': {
            'type': 'object',
            'properties': {'key': {'type': 'string', 'description': '发票标识'}},
            'required': ['key'],
            'additionalProperties': False,
        },
        'handler': tool_raw_text,
    },
    {
        'name': 'invoice_rescan',
        'description': '重新扫描发票目录，刷新解析结果。修复规则变更后可调用。',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'handler': tool_rescan,
    },
    {
        'name': 'invoice_rules_list',
        'description': '查看已配置的修复规则。',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'handler': tool_rules_list,
    },
    {
        'name': 'invoice_rule_add',
        'description': '新增一条修复规则，用于修正解析错误的字段（如金额）。',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'match': {
                    'type': 'object',
                    'description': '匹配条件，如 {"filename_contains": "4282374901"}',
                },
                'field': {'type': 'string', 'description': '要修正的字段，如 amount'},
                'value': {'description': '修正后的值'},
                'note': {'type': 'string', 'description': '修复说明'},
            },
            'required': ['match', 'field', 'value'],
            'additionalProperties': False,
        },
        'handler': tool_rules_add,
    },
    {
        'name': 'invoice_rule_delete',
        'description': '删除一条修复规则。',
        'inputSchema': {
            'type': 'object',
            'properties': {'id': {'type': 'string', 'description': '规则 ID'}},
            'required': ['id'],
            'additionalProperties': False,
        },
        'handler': tool_rules_delete,
    },
    {
        'name': 'invoice_export',
        'description': '导出 Excel 统计表（含汇总、分类小计、异常清单三个工作表）。',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'status': {'type': 'string', 'description': '只导出某状态，如 normal'},
                'q': {'type': 'string', 'description': '只导出匹配关键字的发票'},
                'filename': {'type': 'string', 'description': '输出文件名（默认自动命名）'},
                'label': {'type': 'string', 'description': '表格标题里的范围说明'},
            },
            'additionalProperties': False,
        },
        'handler': tool_export,
    },
]

TOOL_MAP = {t['name']: t for t in TOOLS}


# ---------------------------------------------------------------- JSON-RPC
def rpc_result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def rpc_error(id_, code, message, data=None):
    err = {'code': code, 'message': message}
    if data is not None:
        err['data'] = data
    return {'jsonrpc': '2.0', 'id': id_, 'error': err}


def handle_request(cfg, req):
    """处理一条 JSON-RPC 请求，返回响应字典（通知类返回 None）。"""
    if not isinstance(req, dict):
        return rpc_error(None, -32600, 'Invalid Request')

    method = req.get('method')
    id_ = req.get('id')
    params = req.get('params') or {}

    # ---- 通知（无 id，不需要响应）----
    if id_ is None:
        if method == 'notifications/initialized':
            log('client initialized')
        return None

    # ---- 初始化 ----
    if method == 'initialize':
        return rpc_result(id_, {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
        })

    if method == 'ping':
        return rpc_result(id_, {})

    # ---- 工具列表 ----
    if method == 'tools/list':
        return rpc_result(id_, {
            'tools': [
                {
                    'name': t['name'],
                    'description': t['description'],
                    'inputSchema': t['inputSchema'],
                }
                for t in TOOLS
            ],
        })

    # ---- 调用工具 ----
    if method == 'tools/call':
        name = params.get('name')
        args = params.get('arguments') or {}
        tool = TOOL_MAP.get(name)
        if not tool:
            return rpc_error(id_, -32602, f'Unknown tool: {name}')

        try:
            text = tool['handler'](cfg, args)
            return rpc_result(id_, {
                'content': [{'type': 'text', 'text': str(text)}],
                'isError': False,
            })
        except Exception as e:
            log(f'tool {name} failed: {e}\n{traceback.format_exc()}')
            return rpc_result(id_, {
                'content': [{'type': 'text', 'text': f'执行失败：{e}'}],
                'isError': True,
            })

    return rpc_error(id_, -32601, f'Method not found: {method}')


# ---------------------------------------------------------------- 主循环
def main():
    # stdio 必须强制 UTF-8：Windows 默认 GBK 会让中文 JSON 损坏
    try:
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    cfg = api.load_config()
    log(f'MCP server started (pid={os.getpid()})')

    # stdio：逐行读取 JSON-RPC 消息
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            resp = rpc_error(None, -32700, f'Parse error: {e}')
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + '\n')
            sys.stdout.flush()
            continue

        # 支持批量请求
        if isinstance(req, list):
            out = [r for r in (handle_request(cfg, r) for r in req) if r]
        else:
            out = handle_request(cfg, req)

        if out is None:
            continue

        sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
        sys.stdout.flush()

    log('MCP server exiting (stdin closed)')


if __name__ == '__main__':
    main()
