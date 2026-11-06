"""HTTP 端到端自检：真的起一个服务，用 HTTP 打一遍关键路径。

覆盖安全修复里只能在网络层验证的项：
  · Host 头伪造 / DNS rebinding → 403
  · 旧静态数据 demo-data.js → 404
  · 分类建/分配/落盘/回读
  · 导出文件名不可穿越
  · summary 的 pending 分列

不依赖真实发票：先写好 .发票助手/scan-data.json，服务启动后直接读它。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []

ITEMS = [
    {'rel_path': 'a.pdf', 'filename': 'a.pdf', 'status': 'normal', 'amount': 120.0,
     'seller': '甲公司', 'item': '设计服务', 'invoice_no': '1001', 'issue_date': '2026-10-01'},
    {'rel_path': 'b.pdf', 'filename': 'b.pdf', 'status': 'unverified', 'amount': 5.5,
     'seller': '乙公司', 'item': '配件', 'invoice_no': '1002', 'issue_date': '2026-10-02'},
    {'rel_path': 'c.pdf', 'filename': 'c.pdf', 'status': 'dup', 'amount': 120.0,
     'seller': '甲公司', 'item': '设计服务', 'invoice_no': '1001', 'issue_date': '2026-10-01'},
]


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print(f'  PASS  {name}' + (f' — {detail}' if detail else ''))
    except Exception as e:
        FAIL.append((name, repr(e)))
        print(f'  FAIL  {name} — {e!r}')


def main():
    tmp = tempfile.mkdtemp(prefix='ia_http_')
    app = os.path.join(tmp, 'app')
    inv = os.path.join(tmp, 'invoices')
    os.makedirs(app)
    os.makedirs(os.path.join(inv, '.发票助手'))

    for f in os.listdir(HERE):
        if f.endswith('.py'):
            shutil.copy(os.path.join(HERE, f), app)
    shutil.copytree(os.path.join(HERE, 'web'), os.path.join(app, 'web'))

    with open(os.path.join(inv, '.发票助手', 'scan-data.json'), 'w', encoding='utf-8') as f:
        json.dump({'items': ITEMS, 'summary': {'files': 3, 'counted': 2, 'total': 125.5}}, f,
                  ensure_ascii=False)

    port = free_port()
    env = dict(os.environ, INVOICE_ROOT=inv)
    proc = subprocess.Popen([sys.executable, 'serve_api.py', '--port', str(port), '--no-browser'],
                            cwd=app, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace')
    base = f'http://127.0.0.1:{port}'
    try:
        _wait_up(base, proc)
        run_checks(base, port, inv)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f'通过 {len(PASS)} 项，失败 {len(FAIL)} 项')
    for n, e in FAIL:
        print(f'  · {n}: {e}')
    return 0 if not FAIL else 1


def _wait_up(base, proc):
    for _ in range(120):
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ''
            raise SystemExit(f'服务启动失败：\n{out}')
        try:
            urllib.request.urlopen(base + '/', timeout=1).read()
            return
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.25)
    raise SystemExit('服务 30 秒内没起来')


def _req(base, path, token=None, method='GET', body=None, host=None):
    url = base + urllib.parse.quote(path, safe='/?=&')
    data = json.dumps(body).encode('utf-8') if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('Content-Type', 'application/json')
    if token:
        r.add_header('X-Api-Token', token)
    if host:
        # urllib 会用显式 Host 头覆盖自动生成的那个
        r.add_header('Host', host)
        r.unredirected_hdrs['Host'] = host
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read().decode('utf-8')
            return resp.status, (json.loads(raw) if raw.strip().startswith(('{', '[')) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def run_checks(base, port, inv):
    token_box = {}

    def t_bootstrap():
        st, d = _req(base, '/api/bootstrap')
        assert st == 200, st
        token_box['t'] = d['token']
        return '拿到令牌'

    def t_bootstrap_bad_host():
        for host in ('evil.com', f'127.0.0.1.nip.io:{port}', f'localhost:{port + 1}'):
            st, _ = _req(base, '/api/bootstrap', host=host)
            assert st == 403, f'Host={host} -> {st}'
        return '三个伪造 Host 全被挡'

    def t_static_leak():
        for p in ('/demo-data.js', '/web/demo-data.js'):
            st, _ = _req(base, p)
            assert st == 404, f'{p} -> {st}'
        return '/demo-data.js 404'

    def t_list():
        st, d = _req(base, '/api/invoices', token=token_box['t'])
        assert st == 200, st
        assert d['total'] == 3, d.get('total')
        assert d['root'] == os.path.abspath(inv), d.get('root')
        return '3 条，root 正确'

    def t_token_required():
        st, _ = _req(base, '/api/invoices')
        assert st == 401, st
        st, _ = _req(base, '/api/invoices?token=' + token_box['t'])
        assert st == 401, f'query token 竟然放行：{st}'
        return '无头 401，?token= 不放行'

    def t_summary():
        st, d = _req(base, '/api/summary', token=token_box['t'])
        assert st == 200, st
        assert d['counted'] == 2, d
        assert abs(d['total'] - 125.5) < 1e-6, d
        assert d['pending'] == 1 and abs(d['pending_total'] - 5.5) < 1e-6, d
        return '125.5 / 待确认 5.5'

    def t_category_flow():
        st, d = _req(base, '/api/categories', method='POST', token=token_box['t'],
                     body={'name': '甲类', 'color': 'green'})
        assert st in (200, 201), (st, d)
        cid = d['category']['id']
        # 字段名写错必须报错，不能静默成功
        st, d = _req(base, '/api/categories/assign', method='POST', token=token_box['t'],
                     body={'ids': ['a.pdf'], 'category': cid})
        assert 'error' in d, f'错字段名竟然返回 {d}'

        st, d = _req(base, '/api/categories/assign', method='POST', token=token_box['t'],
                     body={'rel_paths': ['a.pdf'], 'category': cid})
        assert st == 200, (st, d)
        assert d['result']['assigned'] == 1, d
        # 落盘
        p = os.path.join(inv, '.发票助手', 'categories.json')
        assert os.path.isfile(p), 'categories.json 没落盘'
        snap = json.load(open(p, encoding='utf-8'))
        assert snap['assignments'].get('a.pdf') == cid, snap
        st, d = _req(base, '/api/invoices', token=token_box['t'])
        got = [i for i in d['items'] if i['rel_path'] == 'a.pdf'][0]
        assert got.get('category') == cid, got

        # 改名
        st, d = _req(base, '/api/categories/' + cid, method='PATCH',
                     token=token_box['t'], body={'name': '甲类改名'})
        assert st == 200 and d['category']['name'] == '甲类改名', d
        # 折叠
        st, d = _req(base, '/api/categories/' + cid, method='PATCH',
                     token=token_box['t'], body={'collapsed': True})
        assert d['category']['collapsed'] is True, d
        snap = json.load(open(p, encoding='utf-8'))
        assert snap['categories'][0]['collapsed'] is True, snap
        # 删除：返回解除的分配数，且分配表清空
        st, d = _req(base, '/api/categories/' + cid, method='DELETE', token=token_box['t'])
        assert d['result']['unassigned'] == 1, d
        snap = json.load(open(p, encoding='utf-8'))
        assert snap['assignments'] == {}, snap
        return '建 → 分配 → 改名 → 折叠 → 删除，全程落盘'

    def t_export_traversal():
        st, d = _req(base, '/api/export', method='POST', token=token_box['t'],
                     body={'filename': '../../evil'})
        assert st == 200, (st, d)
        out = os.path.join(inv, '导出')
        names = os.listdir(out)
        assert len(names) == 1, names
        assert names[0].startswith('evil') and names[0].endswith('.xlsx'), names
        assert not os.path.exists(os.path.join(tmp_of(inv), 'evil.xlsx')), '穿越成功了'
        return names[0]

    def t_export_excel_semantics():
        """导出的合计要等于后端总额（当年 SUMIF 写错列，打开就变大）。"""
        import openpyxl
        out = os.path.join(inv, '导出')
        p = os.path.join(out, os.listdir(out)[0])
        wb = openpyxl.load_workbook(p)
        ws = wb['发票汇总']
        cached = openpyxl.load_workbook(p, data_only=True)['发票汇总']['C3'].value
        assert cached is not None, 'C3 没有缓存值（打开会空白）'
        assert abs(float(cached) - 125.5) < 1e-6, cached
        return f'C3={cached}'

    check('启动：/api/bootstrap 免令牌但校验 Host', t_bootstrap)
    check('安全：伪造 Host 一律 403', t_bootstrap_bad_host)
    check('安全：旧静态数据不再直出', t_static_leak)
    check('安全：令牌只认请求头', t_token_required)
    check('列表：条目数与扫描根', t_list)
    check('汇总：待确认金额单列', t_summary)
    check('分类：建/分配/改名/折叠/删除全链路', t_category_flow)
    check('导出：文件名不可穿越', t_export_traversal)
    check('导出：合计与后端一致', t_export_excel_semantics)


def tmp_of(inv):
    return os.path.dirname(os.path.abspath(inv))


if __name__ == '__main__':
    sys.exit(main())
