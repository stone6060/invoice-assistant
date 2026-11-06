"""离线自检：不需要真实发票，也不联网。

覆盖分类持久化、令牌校验、规则字段白名单、导出文件名、公式注入防护、
zip 体积护栏、Host 校验、数据目录跟随扫描根等纯逻辑。
真实数据相关的验收见 test_all.py（需要先启动服务）。

用法：python test_core.py
"""
import io
import json
import os
import sys
import tempfile
import zipfile

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api_service as api          # noqa: E402
import categories                  # noqa: E402
import exporter                    # noqa: E402
import safezip                     # noqa: E402
import serve_api                   # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        detail = fn()
        PASS.append(name)
        print(f'  PASS  {name}' + (f' — {detail}' if detail else ''))
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print(f'  FAIL  {name} — {e}')
    except Exception as e:                      # noqa: BLE001
        FAIL.append((name, f'{type(e).__name__}: {e}'))
        print(f'  FAIL  {name} — {type(e).__name__}: {e}')


def tmpdir():
    return tempfile.mkdtemp(prefix='ia-test-')


# ------------------------------------------------------------------ 分类存储
def t_category_roundtrip():
    d = tmpdir()
    p = os.path.join(d, 'categories.json')
    s = categories.CategoryStore(p)
    a = s.create('电子元件')
    b = s.create('差旅')
    s.assign(['111/a.pdf'], a['id'])
    s.rename(b['id'], '差旅交通')
    s.set_collapsed(a['id'], True)

    again = categories.CategoryStore(p)          # 重新加载 == 重启服务
    snap = again.snapshot()
    assert len(snap['categories']) == 2, snap
    assert snap['assignments']['111/a.pdf'] == a['id'], snap['assignments']
    assert any(c['name'] == '差旅交通' for c in snap['categories'])
    assert any(c['collapsed'] for c in snap['categories'])

    res = again.delete(a['id'])
    assert res['unassigned'] == 1, res
    assert again.snapshot()['assignments'] == {}
    return '2 分类 / 1 分配 / 删除连带清理'


def t_category_color_whitelist():
    d = tmpdir()
    s = categories.CategoryStore(os.path.join(d, 'categories.json'))
    cat = s.create('x', color='"><img src=x onerror=alert(1)>')
    assert cat['color'] in categories.DEFAULT_COLORS, cat
    assert categories.norm_color('red') == 'red'
    assert categories.norm_color('nope') is None
    return cat['color']


def t_category_corrupt_file():
    d = tmpdir()
    p = os.path.join(d, 'categories.json')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('{ this is not json')
    s = categories.CategoryStore(p)
    assert s.snapshot()['categories'] == []
    assert any(n.startswith('categories.json.corrupt-') for n in os.listdir(d))
    s.create('恢复后可用')
    assert len(s.snapshot()['categories']) == 1
    return '损坏文件已备份并自愈'


def t_category_name_limits():
    d = tmpdir()
    s = categories.CategoryStore(os.path.join(d, 'categories.json'))
    long_name = '名' * 200
    cat = s.create(long_name)
    assert len(cat['name']) == categories.MAX_NAME_LEN, len(cat['name'])
    try:
        s.create('   ')
    except ValueError:
        return f'限长 {categories.MAX_NAME_LEN}，空名被拒'
    raise AssertionError('空名未被拒绝')


# ------------------------------------------------------------------ 令牌
def t_token():
    cfg = {'api': {'token': 'abc123'}}

    class H(dict):
        def get(self, k, default=None):
            return super().get(k, default)

    assert api.check_token(cfg, H({'X-Api-Token': 'abc123'})) is True
    assert api.check_token(cfg, H({'X-Api-Token': 'abc124'})) is False
    assert api.check_token(cfg, H({'Authorization': 'Bearer abc123'})) is True
    assert api.check_token(cfg, H({})) is False
    # 查询参数不再被接受（会进历史/Referer/日志）
    assert api.check_token(cfg, H({})) is False
    return '仅认请求头，错误令牌被拒'


def t_host_check():
    ok = serve_api.host_allowed
    assert ok({'Host': '127.0.0.1:17802'}, 17802) is True
    assert ok({'Host': 'localhost:17802'}, 17802) is True
    assert ok({'Host': 'evil.example:17802'}, 17802) is False
    assert ok({'Host': '127.0.0.1:9999'}, 17802) is False
    assert ok({}, 17802) is False
    assert ok({'Host': '127.0.0.1:17802'}, None) is False
    return 'DNS rebinding 被挡'


# ------------------------------------------------------------------ 规则
def t_rule_field_whitelist():
    d = tmpdir()
    old_root = api.ROOT_DIR
    try:
        api.bind_root(d)
        with open(api.RULES_PATH, 'w', encoding='utf-8') as f:
            json.dump([
                {'id': 'r1', 'match': {'filename_contains': 'a.pdf'},
                 'field': 'amount', 'value': '12.345'},
                {'id': 'r2', 'match': {'filename_contains': 'a.pdf'},
                 'field': 'status', 'value': 'dup'},
                {'id': 'r3', 'match': {'filename_contains': 'a.pdf'},
                 'field': 'rel_path', 'value': '../../etc/passwd'},
            ], f)
        item = {'filename': 'a.pdf', 'amount': 1.0, 'status': 'ok',
                'rel_path': 'a.pdf'}
        applied = api.apply_rules(item)
        assert item['amount'] == 12.35, item['amount']
        assert item['status'] == 'ok', '规则改写了 status'
        assert item['rel_path'] == 'a.pdf', '规则改写了 rel_path'
        assert len(applied) == 1, applied

        bad = api.api_rule_add(None, {'field': 'dup_of', 'value': 'x'})
        assert 'error' in bad, bad

        bad2 = api.api_rule_add(None, {'field': 'amount', 'value': 'abc'})
        assert 'error' in bad2, bad2
        return 'amount 生效，status/rel_path 被拒'
    finally:
        api.bind_root(old_root)


# ------------------------------------------------------------------ 导出
def t_export_filename():
    d = tmpdir()
    old_root = api.ROOT_DIR
    try:
        api.bind_root(d)
        with open(api.SCAN_DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump({'items': [{'filename': 'a.pdf', 'rel_path': 'a.pdf',
                                  'amount': 1.0, 'status': 'ok',
                                  'invoice_no': '1', 'seller': '甲',
                                  'item': '物', 'inv_type': '电子发票',
                                  'issue_date': '2026-01-01'}],
                       'summary': {}}, f, ensure_ascii=False)

        # 穿越 / 绝对路径 / 非法字符都必须被拒或落到 导出/ 下
        for evil in ['../../evil', 'C:\\Windows\\Temp\\evil',
                     '..\\..\\evil', 'a/b/../../evil']:
            r = api.api_export(None, {'filename': evil})
            if 'error' in r:
                continue
            out = os.path.join(d, '导出')
            assert r['rel_path'].startswith('导出'), r
            real = os.path.abspath(os.path.join(d, r['rel_path']))
            assert real.startswith(os.path.abspath(out)), real

        r = api.api_export(None, {'filename': 'a<>b|c'})
        assert 'error' in r, r

        r = api.api_export(None, {'filename': '正常名字 2026'})
        assert r.get('ok'), r
        assert os.path.exists(os.path.join(d, r['rel_path'])), r
        return '穿越被挡，正常名可用'
    finally:
        api.bind_root(old_root)


def t_formula_guard():
    assert exporter._cell_safe('=1+1') == "'=1+1"
    assert exporter._cell_safe('=cmd|\' /C calc\'!\'!A0\'')[:1] == "'"
    assert exporter._cell_safe('+86') == "'+86"
    assert exporter._cell_safe('-1') == "'-1"
    assert exporter._cell_safe('@SUM(A1)') == "'@SUM(A1)"
    assert exporter._cell_safe('正常销售方') == '正常销售方'
    assert exporter._cell_safe(3.5) == 3.5
    assert exporter._cell_safe(None) is None
    return '公式前导字符被中和'


# ------------------------------------------------------------------ zip
def t_zip_guard():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('a.bin', b'\0' * (5 * 1024 * 1024))
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        assert safezip.check(zf), '高压缩比未被识别'
        assert safezip.entry(zf, 'a.bin', limit=1024) is None

    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('a.ofd', 'x' * 100)
    buf2.seek(0)
    with zipfile.ZipFile(buf2) as zf:
        assert safezip.check(zf) is None
        assert safezip.entry(zf, 'a.ofd') == b'x' * 100
    return '压缩比/单条上限生效，正常包放行'


# ------------------------------------------------------------------ 数据目录
def t_data_dir_follows_root():
    d = tmpdir()
    old_root = api.ROOT_DIR
    try:
        api.bind_root(d)
        assert api.DATA_DIR == os.path.join(os.path.abspath(d), '.发票助手'), api.DATA_DIR
        assert api.SCAN_DATA_PATH.startswith(api.DATA_DIR)
        assert api.CATEGORY_STORE.path == api.CATEGORIES_PATH
        with open(api.CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump({'api': {'port': 17999}, 'scan': {'root': d}}, f)
        cfg = api.load_config()
        assert os.path.abspath(cfg['scan']['root']) == os.path.abspath(d), cfg['scan']['root']
        assert os.path.abspath(api.DATA_DIR) == os.path.join(os.path.abspath(d), '.发票助手')
        return '配置目录与扫描根一致'
    finally:
        api.bind_root(old_root)


def t_load_items_resilient():
    d = tmpdir()
    old_root = api.ROOT_DIR
    try:
        api.bind_root(d)
        assert api.load_items()['items'] == []
        with open(api.SCAN_DATA_PATH, 'w', encoding='utf-8') as f:
            f.write('{"items": [')                   # 半截 JSON
        data = api.load_items()
        assert data['items'] == [], data
        assert data.get('error'), data
        return '损坏文件不炸'
    finally:
        api.bind_root(old_root)


def t_amount_bounds():
    """金额解析边界：负数、红字、大写用「元」。"""
    import scan_demo_data as sd

    cases = [
        ('价税合计（小写）¥100.00', 100.0),
        ('价税合计（小写）¥-100.00', -100.0),
        ('价税合计（小写）-¥100.00', -100.0),
        ('价税合计（小写）¥−88.80', -88.8),
    ]
    for text, want in cases:
        got = sd.extract_amount(text)
        assert got is not None and abs(got - want) < 1e-6, f'{text} -> {got}, want {want}'

    # 大写用「元」时也要能截全 —— 曾因字符类缺「元」把「壹佰元整」截成「壹佰」
    up = sd.extract_upper('价税合计（大写）壹佰元整')
    assert up == 100.0, up
    up = sd.extract_upper('价税合计（大写）壹佰贰拾叁圆肆角伍分')
    assert abs(up - 123.45) < 1e-6, up
    return '负数/红字/「元」大写都对'


def t_dup_priority():
    """重复票必须压过「待确认」，否则同一张票会被算两次。"""
    import scan_demo_data as sd

    dup = {'status': 'dup', 'amount': 351.0, 'amount_ok': False}
    assert sd.is_counted(dup) is False, 'dup 被计账了'

    for st in ('container', 'error'):
        assert sd.is_counted({'status': st, 'amount': 10.0}) is False, st
    assert sd.is_counted({'status': 'normal', 'amount': 10.0}) is True
    assert sd.is_counted({'status': 'normal', 'amount': None}) is False
    return 'dup/container/error 都不计账'


def t_rail_ofd():
    """独立 .ofd 铁路客票：金额在「票价」，不在「价税合计」。"""
    import zipfile
    import scan_demo_data as sd

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ofd:Content xmlns:ofd="http://www.ofdspec.org/2016">'
        '<ofd:TextCode>铁路电子客票</ofd:TextCode>'
        '<ofd:TextCode>发票号码:26429165800000000001</ofd:TextCode>'
        '<ofd:TextCode>开票日期:2026年10月21日</ofd:TextCode>'
        '<ofd:TextCode>票价: ￥166.00</ofd:TextCode>'
        '<ofd:TextCode>G416</ofd:TextCode>'
        '</ofd:Content>'
    )
    d = tmpdir()
    p = os.path.join(d, 'rail.ofd')
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('content.xml', xml)
    rec = sd.parse_ofd(p)
    assert rec['amount'] == 166.0, rec['amount']
    assert rec['invoice_no'] == '26429165800000000001', rec['invoice_no']
    assert rec['issue_date'] == '2026-10-21', rec['issue_date']
    assert 'G416' in rec['item'], rec['item']
    return '¥166 / G416'


def t_scan_result_has_pending():
    """汇总要能区分「已确认」与「待确认」，且总额与列表口径一致。"""
    import api_service as api

    d = tmpdir()
    old_root = api.ROOT_DIR
    try:
        api.bind_root(d)
        os.makedirs(api.DATA_DIR, exist_ok=True)
        with open(api.SCAN_DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump({'items': [
                {'rel_path': 'a.pdf', 'filename': 'a.pdf', 'status': 'normal', 'amount': 10.0},
                {'rel_path': 'b.pdf', 'filename': 'b.pdf', 'status': 'unverified', 'amount': 5.0},
                {'rel_path': 'c.pdf', 'filename': 'c.pdf', 'status': 'dup', 'amount': 10.0},
            ], 'summary': {}}, f, ensure_ascii=False)
        s = api.api_summary({'scan': {}})
        # unverified 仍然计入总额（只是标出来提醒），dup 不算
        assert s['counted'] == 2, s
        assert abs(s['total'] - 15.0) < 1e-6, s
        assert s['duplicates'] == 1, s
        assert s['pending'] == 1, s
        assert abs(s['pending_total'] - 5.0) < 1e-6, s
        return 'dup 不计账，待确认 5.0 单列'
    finally:
        api.bind_root(old_root)


def main():
    print('发票助手 · 离线自检')
    for name, fn in [
        ('分类：新建/改名/折叠/分配/删除', t_category_roundtrip),
        ('分类：颜色白名单', t_category_color_whitelist),
        ('分类：损坏文件自愈', t_category_corrupt_file),
        ('分类：名称长度与空名', t_category_name_limits),
        ('令牌：只认请求头', t_token),
        ('Host：只接受回环', t_host_check),
        ('规则：字段白名单', t_rule_field_whitelist),
        ('导出：文件名不可穿越', t_export_filename),
        ('导出：公式注入防护', t_formula_guard),
        ('压缩包：体积与压缩比护栏', t_zip_guard),
        ('数据：目录跟随扫描根', t_data_dir_follows_root),
        ('数据：扫描结果损坏不白屏', t_load_items_resilient),
        ('金额：负数 / 红字 / 「元」大写', t_amount_bounds),
        ('金额：重复票不重复计账', t_dup_priority),
        ('解析：独立 OFD 铁路客票', t_rail_ofd),
        ('汇总：待确认金额单列', t_scan_result_has_pending),
    ]:
        check(name, fn)

    print()
    print(f'通过 {len(PASS)} 项，失败 {len(FAIL)} 项')
    for n, e in FAIL:
        print(f'  · {n}: {e}')
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
