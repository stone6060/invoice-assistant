"""归档 / 撤销的离线自检：只用临时目录里的假文件，绝不触碰真实发票。

用法：python tests/test_archive.py
"""
import io
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

import api_service as api          # noqa: E402

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


# ---------------------------------------------------------------- 沙箱准备

class Sandbox:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix='ia-arch-')
        self.names = ['a.pdf', 'b.pdf', 'c.pdf']
        for i, n in enumerate(self.names):
            with io.open(os.path.join(self.root, n), 'w', encoding='utf-8') as f:
                f.write('fake invoice %d' % i)
        api.bind_root(self.root)

        data = {
            'items': [
                {'rel_path': n, 'filename': n, 'amount': 10.0 + i,
                 'status': 'normal', 'amount_ok': True}
                for i, n in enumerate(self.names)
            ],
            'summary': {'files': 3, 'counted': 3, 'total': 33.0},
        }
        with io.open(api.SCAN_DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

        api.CATEGORY_STORE = api.CategoryStore(api.CATEGORIES_PATH)
        self.cat = api.CATEGORY_STORE.create('报销甲')['id']
        api.CATEGORY_STORE.assign(['a.pdf', 'b.pdf'], self.cat)

    def path(self, rel):
        return os.path.join(self.root, rel.replace('/', os.sep))


sb = Sandbox()


# ---------------------------------------------------------------- 用例

def t_dry_run_keeps_files():
    r = api.api_archive(None, {'rel_paths': ['a.pdf', 'b.pdf'], 'dry_run': True})
    assert r.get('plan'), r
    assert len(r['plan']) == 2, r
    assert all(p['to'].startswith('报销甲/') for p in r['plan']), r
    # 预演不得移动任何文件
    assert os.path.isfile(sb.path('a.pdf')) and os.path.isfile(sb.path('b.pdf')), 'dry_run 动了文件'
    return '计划 2 张，磁盘未变'


def t_archive_moves_and_rekeys():
    r = api.api_archive(None, {'rel_paths': ['a.pdf', 'b.pdf']})
    assert r.get('moved') == 2, r
    assert os.path.isfile(sb.path('报销甲/a.pdf')), '未移动到分类目录'
    assert not os.path.isfile(sb.path('a.pdf')), '原件仍在'
    # 分类关系必须跟着文件走
    snap = api.CATEGORY_STORE.snapshot()
    assert '报销甲/a.pdf' in snap['assignments'], snap['assignments']
    assert 'a.pdf' not in snap['assignments'], '旧键未清'
    return '移动 2 张，分类键已迁移'


def t_unclassified_skipped():
    r = api.api_archive(None, {'rel_paths': ['c.pdf']})
    assert r.get('moved') == 0, r
    assert r['skipped'] and '分类' in r['skipped'][0]['reason'], r
    assert os.path.isfile(sb.path('c.pdf')), 'c.pdf 不该被移动'
    return '未分类被跳过'


def t_name_conflict_no_overwrite():
    # 再放一张同名文件，归档后应是「a (2).pdf」
    dst_dir = sb.path('报销甲')
    os.makedirs(dst_dir, exist_ok=True)
    with io.open(os.path.join(dst_dir, 'a.pdf'), 'w', encoding='utf-8') as f:
        f.write('existing')
    with io.open(sb.path('d.pdf'), 'w', encoding='utf-8') as f:
        f.write('new one')
    items = json.load(io.open(api.SCAN_DATA_PATH, encoding='utf-8'))
    items['items'].append({'rel_path': 'd.pdf', 'filename': 'd.pdf',
                           'amount': 1.0, 'status': 'normal', 'amount_ok': True})
    with io.open(api.SCAN_DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)
    api.CATEGORY_STORE.assign(['d.pdf'], sb.cat)
    r = api.api_archive(None, {'rel_paths': ['d.pdf']})
    assert r.get('moved') == 1, r
    assert os.path.isfile(os.path.join(dst_dir, 'd.pdf')), '目标目录应有 d.pdf'
    # 再归档一张同名 a.pdf，应加 (2)
    with io.open(sb.path('a.pdf'), 'w', encoding='utf-8') as f:
        f.write('dup name')
    items = json.load(io.open(api.SCAN_DATA_PATH, encoding='utf-8'))
    items['items'].append({'rel_path': 'a.pdf', 'filename': 'a.pdf',
                           'amount': 2.0, 'status': 'normal', 'amount_ok': True})
    with io.open(api.SCAN_DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False)
    api.CATEGORY_STORE.assign(['a.pdf'], sb.cat)
    r2 = api.api_archive(None, {'rel_paths': ['a.pdf']})
    assert r2.get('moved') == 1, r2
    n = len([x for x in os.listdir(dst_dir) if x.startswith('a')])
    assert n >= 2, '同名文件应加序号而不是覆盖'
    return '同名加序号，未覆盖'


def t_undo_restores():
    r = api.api_undo(None)
    assert r.get('restored', 0) >= 1, r
    assert os.path.isfile(sb.path('b.pdf')) or os.path.isfile(sb.path('b (2).pdf')), '未还原'
    return f"还原 {r['restored']} 张"


def t_bad_dirname_rejected():
    # 分类名全是非法字符 → 无法作为文件夹名
    cid = api.CATEGORY_STORE.create('///')['id']
    api.CATEGORY_STORE.assign(['c.pdf'], cid)
    r = api.api_archive(None, {'rel_paths': ['c.pdf'], 'dry_run': True})
    assert not r['plan'], r
    assert r['skipped'], r
    return '非法分类名被拒'


def t_path_escape_blocked():
    r = api.api_archive(None, {'rel_paths': ['../outside.pdf'], 'dry_run': True})
    assert not r['plan'], r
    return '目录外路径被拒'


def t_bad_body():
    r = api.api_archive(None, {'rel_paths': 'a.pdf'})
    assert r.get('error'), r
    return '错误入参被拒'


check('归档：预演不移动文件', t_dry_run_keeps_files)
check('归档：移动 + 分类键迁移', t_archive_moves_and_rekeys)
check('归档：未分类跳过', t_unclassified_skipped)
check('归档：同名不覆盖', t_name_conflict_no_overwrite)
check('归档：撤销还原', t_undo_restores)
check('归档：非法分类名', t_bad_dirname_rejected)
check('归档：拒绝目录外路径', t_path_escape_blocked)
check('归档：错误入参', t_bad_body)

print()
print(f'通过 {len(PASS)} / {len(PASS) + len(FAIL)}')
if FAIL:
    for n, e in FAIL:
        print('  FAILED', n, e)
    sys.exit(1)
