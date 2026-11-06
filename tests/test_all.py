"""全功能验收测试：把每个功能都真实跑一遍。"""
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

PASS, FAIL = [], []


def req(method, path, body=None, use_token=True):
    url = BASE + urllib.parse.quote(path, safe='/?=&')
    data = json.dumps(body).encode('utf-8') if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('Content-Type', 'application/json')
    if use_token:
        r.add_header('X-Api-Token', TOKEN)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode('utf-8'))


def check(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  [PASS] {name}  {detail}')
    else:
        FAIL.append(name)
        print(f'  [FAIL] {name}  {detail}')


print('=' * 66)
print('一、鉴权')
print('=' * 66)
code, r = req('GET', '/api/summary', use_token=False)
check('无令牌被拦截', code == 401, f'HTTP {code}')
code, r = req('GET', '/api/summary', use_token=True)
check('带令牌可访问', code == 200, f'HTTP {code}')

print()
print('=' * 66)
print('二、数据查询')
print('=' * 66)
code, s = req('GET', '/api/summary')
check('汇总统计', code == 200 and s['counted'] > 0,
      f"{s['files']} 文件 / {s['counted']} 张 / ¥{s['total']:.2f}")

code, l = req('GET', '/api/invoices?limit=5')
check('列表查询', code == 200 and len(l['items']) > 0, f"返回 {l['returned']}/{l['total']}")

code, l = req('GET', '/api/invoices?q=铁路')
check('关键字搜索', code == 200 and l['total'] > 0, f'命中 {l["total"]} 条')

code, l_all = req('GET', '/api/invoices?limit=5000')
pdf_item = next((i for i in l_all['items'] if i['filename'].lower().endswith('.pdf')), None)
zip_item = next((i for i in l_all['items'] if i['filename'].lower().endswith('.zip')
                 and i.get('status') != 'container'), None)

code, one = req('GET', f'/api/invoice/{pdf_item["rel_path"]}')
check('单张详情', code == 200 and one.get('filename') == pdf_item['filename'],
      one.get('filename', '')[:40])

code, raw = req('GET', f'/api/invoice/{pdf_item["rel_path"]}/text')
check('原文提取（PDF）', code == 200 and 'error' not in raw,
      f'长度 {len(raw.get("text",""))}' if 'error' not in raw else raw.get('error'))

if zip_item:
    code, raw2 = req('GET', f'/api/invoice/{zip_item["rel_path"]}/text')
    ok2 = code == 200 and 'error' not in raw2
    detail = raw2.get('text', '')[:40].replace('\n', ' ') if ok2 else raw2.get('error')
    check('原文提取（铁路票 ZIP）', ok2 and len(raw2.get('text', '')) > 0,
          f'{zip_item["filename"][:20]} → {detail}')

print()
print('=' * 66)
print('三、修复规则')
print('=' * 66)
code, r = req('POST', '/api/rules', {
    'match': {'filename_contains': 'TESTONLY_zzz'},
    'field': 'amount', 'value': 9.99, 'note': '验收测试用'
})
rid = r.get('rule', {}).get('id')
check('新增规则', code == 200 and rid, f'ID={rid}')

code, rl = req('GET', '/api/rules')
check('规则列表', code == 200 and any(x['id'] == rid for x in rl['rules']),
      f'共 {len(rl["rules"])} 条')

code, r = req('DELETE', f'/api/rules/{rid}')
check('删除规则', code == 200 and r.get('deleted') == 1, f"删除 {r.get('deleted')}")

print()
print('=' * 66)
print('四、重新扫描（异步）')
print('=' * 66)
import time
code, r = req('POST', '/api/rescan')
check('启动扫描', code == 200 and r.get('running'), f"started_at={r.get('started_at')}")

final = None
for i in range(90):
    time.sleep(2)
    code, st = req('GET', '/api/scan/status')
    if not st.get('running'):
        final = st
        break
if final is None:
    check('扫描完成', False, '超时 180 秒')
else:
    res = final.get('result') or {}
    check('扫描完成', final.get('error') is None,
          f"{res.get('files')} 文件 / {res.get('counted')} 张 / ¥{res.get('total', 0):.2f}"
          if not final.get('error') else final.get('error'))

print()
print('=' * 66)
print('五、导出 Excel')
print('=' * 66)
code, e = req('POST', '/api/export', {'label': '验收测试'})
ok = code == 200 and e.get('ok')
check('导出成功', ok, e.get('path', e.get('error', '')))
if ok:
    check('文件存在', os.path.exists(e['path']),
          f"{os.path.getsize(e['path']) / 1024:.1f} KB")
    # 验证缓存值（关键：总金额不能是空的）
    import openpyxl
    wb = openpyxl.load_workbook(e['path'], data_only=True)
    ws = wb['发票汇总']
    banner = ws['C3'].value
    check('总金额有缓存值', isinstance(banner, (int, float)), f'C3 = {banner}')
    check('三个工作表', len(wb.sheetnames) == 3, str(wb.sheetnames))

    ws2 = wb['分类小计']
    total_cell = ws2.cell(ws2.max_row, 3).value
    check('分类小计合计', isinstance(total_cell, (int, float)), f'合计 = {total_cell}')

print()
print('=' * 66)
print('六、配置')
print('=' * 66)
code, c = req('GET', '/api/bootstrap', use_token=False)
check('bootstrap 免令牌', code == 200 and c.get('token'), 'ok')
check('MCP 路径有效', os.path.exists(c.get('mcp', {}).get('script', '')),
      c.get('mcp', {}).get('script', '')[-40:])

print()
print('=' * 66)
print(f'结果：通过 {len(PASS)} 项，失败 {len(FAIL)} 项')
print('=' * 66)
if FAIL:
    for f in FAIL:
        print('  FAILED:', f)
    sys.exit(1)
