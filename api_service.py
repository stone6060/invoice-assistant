"""发票助手 · 配置与数据访问层

精简版：去掉局域网访问、CORS 等非必要项，只保留本机回环使用所需的最小配置。
"""
import os
import re
import sys
import contextlib
import json
import threading
import hmac
import secrets
import shutil
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

BASE_DIR = paths.app_dir()                              # …/发票/发票助手
# 扫描根：默认由程序所在位置向上探测，之后跟随 config 里的 scan.root
ROOT_DIR = paths.resolve_root()                         # …/发票
DATA_DIR_NAME = '.发票助手'

DATA_DIR = os.path.join(ROOT_DIR, DATA_DIR_NAME)
CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')
RULES_PATH = os.path.join(DATA_DIR, 'fix_rules.json')
CATEGORIES_PATH = os.path.join(DATA_DIR, 'categories.json')
SCAN_DATA_PATH = os.path.join(DATA_DIR, 'scan-data.json')

from categories import CategoryStore          # noqa: E402
from scan_demo_data import is_counted         # noqa: E402  唯一的「是否计账」判定
CATEGORY_STORE = CategoryStore(CATEGORIES_PATH)


def bind_root(root):
    """把数据目录绑定到真正的扫描根。

    程序可能被放在别处（例如单独 clone 出来的仓库），这时由程序位置推出的
    ROOT_DIR 会和 config 里的 scan.root 不一致。若各处仍用模块级路径，就会
    在两个目录下各写一份 .发票助手（配置、分类、扫描结果各自分裂）。
    """
    global ROOT_DIR, DATA_DIR, CONFIG_PATH, RULES_PATH, CATEGORIES_PATH
    global SCAN_DATA_PATH, CATEGORY_STORE

    root = os.path.abspath(root)
    if root == os.path.abspath(ROOT_DIR):
        return
    ROOT_DIR = root
    DATA_DIR = os.path.join(root, DATA_DIR_NAME)
    CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')
    RULES_PATH = os.path.join(DATA_DIR, 'fix_rules.json')
    CATEGORIES_PATH = os.path.join(DATA_DIR, 'categories.json')
    SCAN_DATA_PATH = os.path.join(DATA_DIR, 'scan-data.json')
    CATEGORY_STORE = CategoryStore(CATEGORIES_PATH)
    os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    'api': {
        'port': 17800,
        'token': '',
    },
    'scan': {
        'root': '',
        'extensions': ['.pdf', '.ofd', '.zip', '.jpg', '.jpeg', '.png'],
        'ignore_dirs': ['.发票助手', '发票助手', '.dsh-acl-recovery'],
    },
}


# ---------------------------------------------------------------- 配置
def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)


def _apply_file(cfg, path):
    """把磁盘上的配置合并进 cfg；文件不存在或损坏时静默跳过。"""
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding='utf-8') as f:
            user = json.load(f)
    except Exception:
        return False
    for k, v in user.items():
        if isinstance(v, dict) and k in cfg:
            cfg[k].update(v)
        else:
            cfg[k] = v
    return True


def load_config():
    ensure_dirs()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    _apply_file(cfg, CONFIG_PATH)

    # 配置里的扫描根可能指向别处（程序目录 ≠ 发票目录）→ 数据目录跟随它
    root = (cfg['scan'].get('root') or '').strip()
    if root and os.path.isdir(root) and os.path.abspath(root) != os.path.abspath(ROOT_DIR):
        bind_root(root)
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        _apply_file(cfg, CONFIG_PATH)
        if not (cfg['scan'].get('root') or '').strip():
            cfg['scan']['root'] = os.path.abspath(root)

    # 清掉历史遗留的臃肿字段
    for legacy in ('allow_lan', 'allow_cors', 'enable_manage_api',
                   'enable_mcp', 'enable_web_call'):
        cfg['api'].pop(legacy, None)

    if not cfg['api'].get('token'):
        cfg['api']['token'] = secrets.token_hex(11)
        save_config(cfg)
    return cfg


def save_config(cfg):
    ensure_dirs()
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def reset_token():
    cfg = load_config()
    cfg['api']['token'] = secrets.token_hex(11)
    save_config(cfg)
    return cfg['api']['token']


# ---------------------------------------------------------------- 修复规则
def load_rules():
    if not os.path.exists(RULES_PATH):
        return []
    try:
        with open(RULES_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_rules(rules):
    ensure_dirs()
    with open(RULES_PATH, 'w', encoding='utf-8') as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def add_rule(rule):
    rules = load_rules()
    rule['id'] = rule.get('id') or 'r_' + secrets.token_hex(4)
    rule['created_at'] = datetime.now().isoformat(timespec='seconds')
    rules.append(rule)
    save_rules(rules)
    return rule


def delete_rule(rule_id):
    rules = load_rules()
    before = len(rules)
    rules = [r for r in rules if r.get('id') != rule_id]
    save_rules(rules)
    return before - len(rules)


# 修复规则只允许改这些「内容字段」。rel_path / status / dup_of / category
# 属于结构或状态字段，若可被规则任意改写，就能把某张票伪装成另一张、
# 或把金额整体篡改而界面依旧显示「正常」。
RULE_FIELDS = {'amount', 'amount_upper', 'seller', 'buyer', 'item',
               'invoice_no', 'issue_date', 'inv_type'}


def apply_rules(item):
    """把修复规则套用到单条发票上，返回被改动的字段。"""
    applied = []
    for r in load_rules():
        m = r.get('match', {})
        hit = False
        if m.get('filename_contains') and m['filename_contains'] in item.get('filename', ''):
            hit = True
        if m.get('invoice_no') and m['invoice_no'] == item.get('invoice_no'):
            hit = True
        if m.get('rel_path') and m['rel_path'] == item.get('rel_path'):
            hit = True
        if not hit:
            continue
        field = r.get('field')
        if field not in RULE_FIELDS:
            continue
        value = r.get('value')
        if field == 'amount':
            try:
                value = round(float(value), 2)
            except (TypeError, ValueError):
                continue
        item[field] = value
        applied.append({'rule': r.get('id'), 'field': field, 'value': value})
    return applied


# ---------------------------------------------------------------- 数据
def load_items():
    """读取扫描结果。

    数据放在 .发票助手/ 而不是 web/ 下：web/ 是静态目录，任何本机进程
    （或借助 DNS rebinding 的网页）都能无令牌直接取，等于鉴权失效。
    """
    if not os.path.exists(SCAN_DATA_PATH):
        return {'items': [], 'summary': {}}
    try:
        with open(SCAN_DATA_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        # 半截/损坏的文件不该让整个页面白屏
        return {'items': [], 'summary': {}, 'error': 'scan-data.json 读取失败'}


def public_item(it):
    keep = ['filename', 'rel_path', 'invoice_no', 'seller', 'buyer', 'issue_date',
            'amount', 'amount_upper', 'amount_ok', 'tax', 'item', 'inv_type',
            'status', 'category', 'source', 'unverified_reason', 'dup_of']
    return {k: it.get(k) for k in keep}


def find_item(key):
    """按 rel_path / 文件名 / 发票号查一条，返回的条目已叠加规则与分类。"""
    items, _data, _n = build_items()
    for it in items:
        if (it.get('rel_path') == key or it.get('filename') == key
                or it.get('invoice_no') == key):
            return it
    return None


# ---------------------------------------------------------------- 业务
def api_health(cfg):
    return {
        'ok': True,
        'app': 'invoice-assistant',
        'version': '0.3.0',
        'time': datetime.now().isoformat(timespec='seconds'),
    }


def build_items(data=None):
    """载入条目并叠加修复规则与分类分配。

    所有对外接口（列表 / 单条 / 汇总 / 导出 / MCP）都走这里：
    以前 summary 不套规则、list 套规则，同一个「总额」能给出两个数。
    """
    if data is None:
        data = load_items()
    items = data.get('items', [])
    n_rules = len(load_rules())
    if n_rules:
        for it in items:
            apply_rules(it)
    CATEGORY_STORE.apply_to_items(items)
    return items, data, n_rules


def api_summary(cfg):
    items, data, n_rules = build_items()
    counted = [i for i in items if is_counted(i)]
    pending = [i for i in items if i.get('status') == 'unverified' and i.get('amount') is not None]
    return {
        'files': len(items),
        'counted': len(counted),
        'total': round(sum(i['amount'] for i in counted), 2),
        # 待确认金额单独列出：总额里含它们，但用户需要知道有多少「可能读错」的钱
        'pending': len(pending),
        'pending_total': round(sum(i['amount'] for i in pending), 2),
        'duplicates': sum(1 for i in items if i.get('status') == 'dup'),
        'unverified': sum(1 for i in items if i.get('status') == 'unverified'),
        'applied_rules': n_rules,
        'scan_error': data.get('error'),
        'generated_at': data.get('summary', {}).get('generated_at'),
    }


def api_list(cfg, query):
    items, data, n_rules = build_items()

    status = query.get('status')
    if status:
        items = [i for i in items if i.get('status') == status]

    kw = (query.get('q') or '').strip().lower()
    if kw:
        items = [i for i in items if kw in json.dumps(i, ensure_ascii=False).lower()]

    try:
        limit = int(query.get('limit', 500))
    except (TypeError, ValueError):
        limit = 500

    return {
        'total': len(items),
        'returned': min(limit, len(items)),
        'root': ROOT_DIR,
        'applied_rules': n_rules,
        # 扫描结果读不出来时不能默默给空列表 —— 前端要能提示「数据损坏」
        'scan_error': data.get('error'),
        'items': [public_item(i) for i in items[:limit]],
    }


def api_get(cfg, key):
    it = find_item(key)
    if not it:
        return None
    apply_rules(it)
    CATEGORY_STORE.apply_to_items([it])
    return public_item(it)


def api_raw_text(cfg, key):
    """返回某张票的原始文本，供 AI 分析排版问题。"""
    it = find_item(key)
    if not it:
        return {'error': 'not found'}

    root = cfg['scan'].get('root') or ROOT_DIR
    path = os.path.join(root, it['rel_path'])

    if not os.path.exists(path):
        for c in [os.path.join(ROOT_DIR, it['rel_path']),
                  os.path.join(BASE_DIR, it['rel_path']),
                  os.path.join(os.path.dirname(ROOT_DIR), it['rel_path'])]:
            if os.path.exists(c):
                path = c
                break

    if not os.path.exists(path):
        return {'error': 'file missing', 'rel_path': it.get('rel_path')}

    low = path.lower()

    # ---- PDF：直接用 pdftotext ----
    if low.endswith('.pdf'):
        exe = paths.find_pdftotext()
        if not exe:
            return {'error': 'pdftotext not found',
                    'hint': '请安装 poppler（含 pdftotext）并将其加入 PATH，'
                            '或设置环境变量 PDFTOTEXT 指向可执行文件',
                    'rel_path': it.get('rel_path')}
        try:
            r = subprocess.run([exe, path, '-'], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=30)
            text = r.stdout.decode('utf-8', errors='ignore')
        except Exception as e:
            return {'error': f'pdftotext failed: {type(e).__name__}'}
        return {'filename': it['filename'], 'text': text,
                'hint': {'amount': it.get('amount'),
                         'amount_upper': it.get('amount_upper'),
                         'status': it.get('status')}}

    # ---- ZIP（铁路电子客票）：解出 OFD 里的 XBRL 字段 ----
    if low.endswith('.zip'):
        import zipfile
        import io
        import xml.etree.ElementTree as ET
        import safezip
        try:
            with zipfile.ZipFile(path) as zf:
                bad = safezip.check(zf)
                if bad:
                    return {'error': bad, 'rel_path': it.get('rel_path')}
                for n in zf.namelist():
                    if not n.endswith('.ofd'):
                        continue
                    blob = safezip.entry(zf, n)
                    if blob is None:
                        return {'error': '压缩包内文件过大，已跳过',
                                'rel_path': it.get('rel_path')}
                    with zipfile.ZipFile(io.BytesIO(blob)) as oz:
                        bad = safezip.check(oz)
                        if bad:
                            return {'error': bad, 'rel_path': it.get('rel_path')}
                        for fn in oz.namelist():
                            if 'rai_issuer' not in fn:
                                continue
                            data = safezip.entry(oz, fn)
                            if data is None:
                                return {'error': '压缩包内文件过大，已跳过'}
                            root = ET.fromstring(data)
                            lines = []
                            for e in root.iter():
                                tag = e.tag.split('}')[-1]
                                txt = (e.text or '').strip()
                                if txt:
                                    lines.append(f'{tag}: {txt}')
                            return {
                                'filename': it['filename'],
                                'text': '\n'.join(lines),
                                'hint': {'amount': it.get('amount'),
                                         'amount_upper': it.get('amount_upper'),
                                         'status': it.get('status')},
                            }
            return {'error': '压缩包内未找到可解析的 OFD'}
        except Exception as e:
            return {'error': f'解析压缩包失败: {type(e).__name__}'}

    return {'error': f'不支持的文件类型: {os.path.splitext(path)[1]}'}


def api_rules_list(cfg):
    return {'rules': load_rules()}


# ---------------------------------------------------------------- 扫描任务
# 扫描互斥锁：Web 按钮与 MCP 工具同时触发时串行化，避免两份进程同时写
# scan-data.json（同时写会互相覆盖，读到半截 JSON）。
_SCAN_LOCK = threading.Lock()

_SCAN_STATE = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'result': None,
    'error': None,
}


def api_rescan_async(cfg):
    """启动后台扫描，立即返回。用 api_scan_status 查进度。"""
    import threading

    if not _SCAN_LOCK.acquire(blocking=False):
        return {'ok': True, 'running': True, 'message': '已有扫描任务在进行中'}

    _SCAN_STATE.update(running=True, started_at=datetime.now().isoformat(timespec='seconds'),
                       finished_at=None, result=None, error=None)

    def worker():
        import scan_demo_data
        root = cfg['scan'].get('root') or ROOT_DIR
        scan_demo_data.ROOT = root
        scan_demo_data.DIR111 = os.path.join(root, '111')
        try:
            scan_demo_data.main()
            _SCAN_STATE['result'] = api_summary(cfg)
        except Exception as e:
            _SCAN_STATE['error'] = str(e)
        finally:
            _SCAN_STATE['running'] = False
            with contextlib.suppress(RuntimeError):
                _SCAN_LOCK.release()
            _SCAN_STATE['finished_at'] = datetime.now().isoformat(timespec='seconds')

    threading.Thread(target=worker, daemon=True).start()
    return {'ok': True, 'running': True, 'started_at': _SCAN_STATE['started_at']}


def api_scan_status(cfg):
    return dict(_SCAN_STATE)


def api_rescan(cfg):
    """同步扫描（供 MCP 用，会阻塞但可设长超时）。"""
    import scan_demo_data
    if not _SCAN_LOCK.acquire(timeout=300):
        return {'error': '已有扫描任务在进行中，请稍后重试'}
    _SCAN_STATE.update(running=True, started_at=datetime.now().isoformat(timespec='seconds'),
                       finished_at=None, result=None, error=None)
    root = cfg['scan'].get('root') or ROOT_DIR
    scan_demo_data.ROOT = root
    scan_demo_data.DIR111 = os.path.join(root, '111')
    try:
        scan_demo_data.main()
    except Exception as e:
        _SCAN_STATE.update(running=False, error=str(e))
        return {'error': str(e)}
    finally:
        _SCAN_STATE['running'] = False
        _SCAN_STATE['finished_at'] = datetime.now().isoformat(timespec='seconds')
        with contextlib.suppress(RuntimeError):
            _SCAN_LOCK.release()
    return {'ok': True, **api_summary(cfg)}


def api_export(cfg, body):
    """导出 Excel，返回文件路径与统计。"""
    import exporter

    body = body or {}
    status_filter = body.get('status')
    keyword = (body.get('q') or '').strip()
    cat_map = body.get('categories') or {}      # rel_path -> 分类名

    data = load_items()
    items = data.get('items', [])

    if status_filter:
        items = [i for i in items if i.get('status') == status_filter]
    if keyword:
        items = [i for i in items
                 if keyword.lower() in json.dumps(i, ensure_ascii=False).lower()]

    for it in items:
        apply_rules(it)
        # 把前端传进来的分类名带上
        nm = cat_map.get(it.get('rel_path')) or cat_map.get(it.get('filename'))
        if nm:
            it['category_name'] = nm

    if not items:
        return {'error': '没有可导出的发票'}

    raw = (body.get('filename') or '').strip()
    # 文件名来自客户端，必须先剥掉目录部分再做白名单，否则 ..\..\ 或
    # C:\...\x.xlsx 会让导出写到任意位置并静默覆盖同名文件。
    raw = os.path.basename(raw.replace('\\', '/'))
    if raw.lower().endswith('.xlsx'):
        raw = raw[:-5]
    if not raw:
        raw = f'发票统计表_{datetime.now():%Y%m%d_%H%M%S}'
    if not re.fullmatch(r'[\w\u4e00-\u9fa5 .\-]{1,64}', raw):
        return {'error': '文件名只能包含中英文、数字、空格、点、下划线和短横线',
                'max_length': 64}

    out_dir = os.path.join(ROOT_DIR, '导出')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, raw + '.xlsx')

    info = exporter.export_excel(items, out_path,
                                 scope_label=body.get('label') or '全部发票')
    return {'ok': True, 'rel_path': os.path.join('导出', raw + '.xlsx'), **info}


def api_rule_add(cfg, body):
    if not body or 'field' not in body:
        return {'error': 'rule 需要 field 字段'}
    if body['field'] not in RULE_FIELDS:
        return {'error': '该字段不允许被规则修改',
                'allowed': sorted(RULE_FIELDS)}
    value = body.get('value')
    if body['field'] == 'amount':
        try:
            body['value'] = round(float(value), 2)
        except (TypeError, ValueError):
            return {'error': 'amount 必须是数字'}
    return {'ok': True, 'rule': add_rule(body)}


def api_rule_delete(cfg, rule_id):
    return {'ok': True, 'deleted': delete_rule(rule_id)}


# ---------------------------------------------------------------- 分类
def api_categories(cfg):
    """返回全部分类与分配关系；前端启动时拉一次即可。"""
    snap = CATEGORY_STORE.snapshot()
    snap['stats'] = CATEGORY_STORE.stats()
    return snap


def api_category_create(cfg, body):
    body = body or {}
    try:
        return {'ok': True, 'category': CATEGORY_STORE.create(
            body.get('name'), body.get('color'))}
    except ValueError as e:
        return {'error': str(e)}


def api_category_update(cfg, cat_id, body):
    """改名或折叠：按 body 里出现了哪个字段走哪条路。"""
    body = body or {}
    try:
        if 'name' in body:
            return {'ok': True, 'category': CATEGORY_STORE.rename(cat_id, body['name'])}
        if 'collapsed' in body:
            return {'ok': True,
                    'category': CATEGORY_STORE.set_collapsed(cat_id, body['collapsed'])}
    except (ValueError, KeyError) as e:
        return {'error': str(e)}
    return {'error': '没有可更新的字段'}


def api_category_delete(cfg, cat_id):
    try:
        return {'ok': True, 'result': CATEGORY_STORE.delete(cat_id)}
    except KeyError as e:
        return {'error': str(e)}


def api_category_assign(cfg, body):
    """批量归类：body = {rel_paths: [...], category: cat_id | null}

    以前字段名写错（例如传 ids）会静默成功、什么都不改；现在缺字段直接报错，
    并且只接受当前扫描结果里真实存在的 rel_path，避免分类表里堆垃圾键。
    """
    body = body or {}
    rel = body.get('rel_paths')
    if not isinstance(rel, list):
        return {'error': 'rel_paths 必须是字符串数组'}

    items, _data, _n = build_items()
    known = {i.get('rel_path') for i in items if i.get('rel_path')}
    if not known:
        return {'error': '扫描结果为空，无法归类（请先重新扫描）'}

    valid = [p for p in rel if isinstance(p, str) and p in known]
    skipped = len(rel) - len(valid)
    try:
        res = CATEGORY_STORE.assign(valid, body.get('category'))
    except (ValueError, KeyError) as e:
        return {'error': str(e)}
    res['skipped'] = skipped
    return {'ok': True, 'result': res}


# ---------------------------------------------------------------- 路由
ROUTES = [
    ('GET', r'^/api/health$', lambda c, q, b, m: api_health(c)),
    ('GET', r'^/api/summary$', lambda c, q, b, m: api_summary(c)),
    ('GET', r'^/api/invoices$', lambda c, q, b, m: api_list(c, q)),
    ('GET', r'^/api/invoice/(?P<key>[^/]+)$',
     lambda c, q, b, m: api_get(c, m['key']) or {'error': 'not found'}),
    ('GET', r'^/api/invoice/(?P<key>[^/]+)/text$',
     lambda c, q, b, m: api_raw_text(c, m['key'])),
    ('GET', r'^/api/rules$', lambda c, q, b, m: api_rules_list(c)),
    ('POST', r'^/api/rules$', lambda c, q, b, m: api_rule_add(c, b)),
    ('DELETE', r'^/api/rules/(?P<id>[^/]+)$',
     lambda c, q, b, m: api_rule_delete(c, m['id'])),
    # 分类持久化（assign 必须排在 /:id 通配之前，否则会被当成 id='assign'）
    ('GET', r'^/api/categories$', lambda c, q, b, m: api_categories(c)),
    ('POST', r'^/api/categories$', lambda c, q, b, m: api_category_create(c, b)),
    ('POST', r'^/api/categories/assign$', lambda c, q, b, m: api_category_assign(c, b)),
    ('PATCH', r'^/api/categories/(?P<id>[^/]+)$',
     lambda c, q, b, m: api_category_update(c, m['id'], b)),
    ('DELETE', r'^/api/categories/(?P<id>[^/]+)$',
     lambda c, q, b, m: api_category_delete(c, m['id'])),
    ('POST', r'^/api/export$', lambda c, q, b, m: api_export(c, b)),
    ('POST', r'^/api/rescan$', lambda c, q, b, m: api_rescan_async(c)),
    ('GET', r'^/api/scan/status$', lambda c, q, b, m: api_scan_status(c)),
    ('GET', r'^/api/config$', lambda c, q, b, m: {
        'api': {'port': c['api'].get('port')},
        'scan': c['scan'],
        'token': c['api'].get('token'),
        'mcp': {
            'command': sys.executable,
            'script': os.path.join(BASE_DIR, 'mcp_server.py'),
        },
    }),
    # 网页自用接口：无需令牌（仅回环），只回显配置，不涉及发票数据
    ('GET', r'^/api/bootstrap$', lambda c, q, b, m: {
        'token': c['api'].get('token'),
        'port': c['api'].get('port'),
        'mcp': {
            'command': sys.executable,
            'script': os.path.join(BASE_DIR, 'mcp_server.py'),
        },
    }),
    ('POST', r'^/api/config/token/reset$', lambda c, q, b, m: {
        'ok': True, 'token': reset_token(),
    }),
]


def match_route(method, path):
    for m, pattern, handler in ROUTES:
        if m != method:
            continue
        mm = re.match(pattern, path)
        if mm:
            return handler, mm.groupdict()
    return None, None


def check_token(cfg, headers):
    """校验令牌。

    只认请求头：?token= 会进浏览器历史、Referer 和任何访问日志，等价于明文外泄。
    """
    expected = cfg['api'].get('token') or ''
    if not expected:
        return True
    got = headers.get('X-Api-Token') or ''
    if not got:
        auth = headers.get('Authorization', '')
        if auth.lower().startswith('bearer '):
            got = auth[7:].strip()
    if not got:
        return False
    return hmac.compare_digest(got, expected)
