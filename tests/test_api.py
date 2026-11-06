"""验证 AI 通过 API 修复发票的完整闭环（修正 URL 编码）。"""
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

sys.stdout.reconfigure(encoding='utf-8')

import json
import os
import sys


def _find_config():
    """按 INVOICE_CONFIG → 当前目录 → 上级目录 找 .发票助手/config.json。"""
    env = os.environ.get('INVOICE_CONFIG')
    if env and os.path.isfile(env):
        return env
    here = os.path.abspath(os.getcwd())
    for cand in (os.path.join(here, '.发票助手', 'config.json'),
                 os.path.join(os.path.dirname(here), '.发票助手', 'config.json')):
        if os.path.isfile(cand):
            return cand
    sys.exit('找不到 .发票助手/config.json；请用 INVOICE_CONFIG=<路径> 指定，'
             '或在发票目录下运行。')


CFG = _find_config()
_CFG = json.load(open(CFG, encoding='utf-8'))
BASE = 'http://127.0.0.1:%d' % int(_CFG.get('api', {}).get('port', 17802))
TOKEN = _CFG['api']['token']


def req(method, path, body=None, token=None):
    url = BASE + urllib.parse.quote(path, safe='/?=&')
    data = json.dumps(body).encode('utf-8') if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('Content-Type', 'application/json')
    if token:
        r.add_header('X-Api-Token', token)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


with open(CFG, encoding='utf-8') as f:
    TOKEN = json.load(f)['api']['token']

print('=' * 62)
print('(1) 无令牌访问 -> 应被拦截')
code, r = req('GET', '/api/summary')
print(f'    HTTP {code}  {r.get("error")}')

print()
print('=' * 62)
print('(2) 带令牌查汇总')
code, s = req('GET', '/api/summary', token=TOKEN)
print(f'    HTTP {code}  {json.dumps(s, ensure_ascii=False)}')

print()
print('=' * 62)
print('(3) AI 找异常票（AI 的入口）')
code, r = req('GET', '/api/invoices?status=unverified&limit=20', token=TOKEN)
print(f'    HTTP {code}  待确认 {r["total"]} 张')

print()
print('=' * 62)
print('(4) AI 取原文分析（胶带票）')
code, lst = req('GET', '/api/invoices?q=4282374901&limit=3', token=TOKEN)
if lst.get('items'):
    key = lst['items'][0]['rel_path']
    print(f'    定位: {lst["items"][0]["filename"][:50]}')
    print(f'    当前金额: {lst["items"][0]["amount"]}')
    code, raw = req('GET', f'/api/invoice/{key}/text', token=TOKEN)
    print(f'    原文 HTTP {code}  长度={len(raw.get("text",""))}')
    for line in raw.get('text', '').splitlines():
        if '小写' in line or '圆' in line:
            print(f'      | {line.strip()!r}')

print()
print('=' * 62)
print('(5) AI 写入修复规则')
rule = {
    'match': {'filename_contains': '4282374901'},
    'field': 'amount',
    'value': 1.50,
    'note': 'PDF 把「¥1」与「.50」拆成两行，按大写「壹圆伍角整」补全为 1.50',
}
code, r = req('POST', '/api/rules', rule, token=TOKEN)
rid = r.get('rule', {}).get('id')
print(f'    HTTP {code}  规则ID={rid}')

print()
print('=' * 62)
print('(6) 验证规则生效')
code, lst = req('GET', '/api/invoices?q=4282374901&limit=3', token=TOKEN)
for i in lst.get('items', []):
    print(f'    {i["filename"][:48]}')
    print(f'      amount={i["amount"]}  upper={i["amount_upper"]}  status={i["status"]}')

print()
print('=' * 62)
print('(7) 规则列表')
code, r = req('GET', '/api/rules', token=TOKEN)
print(f'    HTTP {code}  共 {len(r["rules"])} 条')
for x in r['rules']:
    print(f'      {x["id"]}  {x["field"]}={x["value"]}  {x.get("note","")[:45]}')

print()
print('=' * 62)
print('(8) 删除测试规则')
if rid:
    code, r = req('DELETE', f'/api/rules/{rid}', token=TOKEN)
    print(f'    HTTP {code}  删除 {r.get("deleted")} 条')
