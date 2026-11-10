"""扫描根目录 + 111 文件夹，产出前端演示用数据（真实数据，非假数据）。"""
import os
import re
import sys
import json
import zipfile
import hashlib
import subprocess
import io
import xml.etree.ElementTree as ET
from datetime import datetime

# 打包成窗口程序（pythonw / PyInstaller --windowed）时 stdout 是 None，
# 直接 reconfigure 会在 import 阶段就抛 AttributeError。
try:
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr:
        sys.stderr.reconfigure(encoding='utf-8')
except (AttributeError, ValueError):
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

ROOT = paths.resolve_root()
PDFTOTEXT = paths.find_pdftotext()
DATA_DIR_NAME = '.发票助手'

WHITELIST = ('.pdf', '.ofd', '.zip', '.jpg', '.jpeg', '.png')
SKIP_DIRS = {'.发票助手', '发票助手', '.dsh-acl-recovery', 'node_modules'}
SKIP_NAMES = {'发票统计表.xlsx'}


def out_path():
    """扫描结果写到 .发票助手/ 下，不进 web/ 静态目录（否则免令牌可取）。"""
    return os.path.join(ROOT, DATA_DIR_NAME, 'scan-data.json')


def amount_overrides():
    """个别票据机器识别不准时，由用户自己填的金额修正。

    放在 .发票助手/overrides.json（不入库），不写进源码——源码里的文件名
    会跟着仓库一起公开，等于把真实票据清单贴到 GitHub 上。
    """
    p = os.path.join(ROOT, DATA_DIR_NAME, 'overrides.json')
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def file_hash(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:16]


def _pymupdf_text(path):
    """用 PyMuPDF 按坐标还原视觉阅读顺序。

    sort=True 让输出与 pdftotext 的版面顺序一致（「购 名称：湖南大学」在同一行），
    下游正则因此不必区分数据来源。返回 None 表示 PyMuPDF 不可用或解析失败，
    由调用方退回外部 pdftotext。
    """
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        doc = pymupdf.open(path)
        try:
            return '\n'.join(page.get_text('text', sort=True) for page in doc)
        finally:
            doc.close()
    except Exception:
        return None


def pdf_text(path):
    """PDF → 文本：优先内置 PyMuPDF，没有就退回外部 pdftotext。"""
    text = _pymupdf_text(path)
    if text is not None:
        return text
    if not PDFTOTEXT:
        return ''
    try:
        r = subprocess.run([PDFTOTEXT, path, '-'], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=30)
        return r.stdout.decode('utf-8', errors='ignore')
    except Exception:
        return ''


def cn_num(s):
    """人民币大写转数字。"""
    if not s:
        return None
    CN = {'零': 0, '壹': 1, '贰': 2, '叁': 3, '肆': 4, '伍': 5, '陆': 6, '柒': 7, '捌': 8, '玖': 9}
    UNIT = {'拾': 10, '佰': 100, '仟': 1000}
    BIG = {'万': 10000, '亿': 100000000}
    m = re.match(r'^(.*?)圆(.*)$', s.replace('元', '圆'))
    if m:
        ip, fp = m.group(1), m.group(2)
    else:
        ip, fp = '', s
    total = section = number = 0
    for ch in ip:
        if ch in CN:
            number = CN[ch]
        elif ch in UNIT:
            section += (number if number else 1) * UNIT[ch]
            number = 0
        elif ch in BIG:
            section += number
            total += section * BIG[ch]
            section = number = 0
    total += section + number
    j = re.search(r'([零壹贰叁肆伍陆柒捌玖])角', fp)
    f = re.search(r'([零壹贰叁肆伍陆柒捌玖])分', fp)
    return total + (CN[j.group(1)] / 10 if j else 0) + (CN[f.group(1)] / 100 if f else 0)


def extract_amount(text):
    """提取价税合计（小写）。

    PDF 文字层常被排版拆散，例如这张真实票：
        （小写）¥1
        .50
    所以不能只靠一条正则，要先「把散落的字符拼回去」，再多级降级匹配。
    关键点：大写金额是可靠锚点，当小写读出来与大写不符时，以大写为准。
    """
    if not text:
        return None

    # ---------- 预处理：还原被拆散的金额 ----------
    flat = re.sub(r'[\r\n]+', ' ', text)          # 换行变空格
    flat = re.sub(r'\s{2,}', ' ', flat)           # 压缩多余空格
    flat = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', flat)  # 1 . 50 -> 1.50

    # 大写金额作为锚点（最可靠）
    upper = extract_upper(text)

    def _signed(sa, sb, num):
        """带符号金额：`-¥100`、`¥-100`、`−100` 都要认成负数（红字发票）。"""
        v = _to_float(num)
        if v is None:
            return None
        return -abs(v) if (sa or sb) else v

    # ---------- 优先：从「小写」标记附近取完整数字 ----------
    m = re.search(
        r'[（(]\s*小写\s*[）)]\s*([-−]?)\s*[¥￥]?\s*([-−]?)\s*([\d,]+(?:\.\d{1,2})?)', flat)
    if m:
        low = _signed(m.group(1), m.group(2), m.group(3))
        if low is not None:
            # 小写是整数但大写有小数 → 说明小数部分被拆走了，用大写补全
            if upper is not None and abs(abs(low) - upper) > 0.005:
                if abs(abs(low) - int(upper)) < 0.005 and abs(upper - round(upper)) > 0.0001:
                    return -upper if low < 0 else upper
            return low

    # ---------- 次选：金额在前、(小写) 在后 ----------
    m = re.search(
        r'([-−]?)\s*[¥￥]\s*([-−]?)\s*([\d,]+\.\d{1,2})\s*[（(]\s*小写\s*[）)]', flat)
    if m:
        low = _signed(m.group(1), m.group(2), m.group(3))
        if low is not None:
            return low

    # ---------- 再次：价税合计后紧跟金额 ----------
    m = re.search(
        r'价税合计[^\d¥￥\-−]{0,20}([-−]?)\s*[¥￥]?\s*([-−]?)\s*([\d,]+\.\d{1,2})', flat)
    if m:
        low = _signed(m.group(1), m.group(2), m.group(3))
        if low is not None:
            return low

    # ---------- 兜底：用大写金额 ----------
    return upper


def _to_float(s):
    try:
        return float(str(s).replace(',', ''))
    except (ValueError, TypeError):
        return None


def extract_upper(text):
    """取「价税合计（大写）」的中文金额并换算成数字，作为交叉校验与兜底来源。"""
    if not text:
        return None

    flat = re.sub(r'[\r\n]+', ' ', text)
    flat = re.sub(r'\s{2,}', ' ', flat)

    CN_CH = '零壹贰叁肆伍陆柒捌玖'
    mu = re.search(
        rf'价税合计\s*[（(]\s*大写\s*[）)]\s*([{CN_CH}拾佰仟万亿圆元角分整]{{2,30}})', flat)
    cand = mu.group(1) if mu else ''

    if not cand:
        pat = (rf'[{CN_CH}拾佰仟万亿]{{1,12}}[圆元]'
               rf'(?:零)?(?:[{CN_CH}]角)?(?:零)?(?:[{CN_CH}]分)?(?:整)?')
        for m2 in re.finditer(pat, flat):
            c = m2.group(0)
            if ('圆' in c or '元' in c) and len(c) >= 2:
                cand = c
                break

    return cn_num(cand) if cand else None


def party_names(text):
    """取购买方 / 销售方的名称。

    两种文字层都要兼容：
      · PyMuPDF（sort=True，默认）：一列一个「名称：」，购/销 与名称同列，
        购买方在左、销售方在右，同一行里并排出现；
      · pdftotext（回退路径）：「购买方」被竖排拆成 购 / 买 / 方 三行，
        名称紧跟在「购」后面的若干行里。
    """

    def clean(raw):
        """从「…名称：XXX<税号/地址/下一列>」里只取 XXX；没有「名称：」返回空。"""
        parts = re.split(r'名\s*称\s*[:：]', raw)
        if len(parts) < 2:
            return ''
        n = re.split(r'\s{2,}', parts[1])[0].strip()
        n = re.split(r'\d{8,}|统一社会信用代码|纳税人识别号|地\s*址|电\s*话|开户行', n)[0].strip()
        # 末尾偶尔粘上竖排框线的单字（买 / 方 / 信 / 息）
        if len(n) > 8 and n[-1] in '购买方信息销售':
            n = n[:-1].strip()
        return n[:60].strip()

    # A. 版面风格：逐行、逐列（多个空格视为列分隔）
    buyer = seller = ''
    for line in text.splitlines():
        if not re.search(r'名\s*称\s*[:：]', line):
            continue
        for col in re.split(r'\s{2,}', line):
            if not re.search(r'名\s*称\s*[:：]', col):
                continue
            n = clean(col)
            if not n:
                continue
            if not buyer and '购' in col:
                buyer = n
            elif not seller and '销' in col:
                seller = n
        if buyer and seller:
            return buyer, seller

    # B. 竖排风格：看「名称：」前面一段有没有「购」/「销」
    #    注意不能把值一起抓进正则——同一行并排两个「名称：」时贪婪匹配会吞掉第二个
    for m in re.finditer(r'名\s*称\s*[:：]', text):
        before = text[max(0, m.start() - 120):m.start()]
        n = clean(text[m.start():m.start() + 120])
        if not n:
            continue
        if not buyer and '购' in before:
            buyer = n
        elif not seller and '销' in before:
            seller = n
        if buyer and seller:
            break
    return buyer, seller


def parse_fields(text, name):
    """从发票纯文本里抽取字段。

    PDF 直接用 pdftotext 的文字层；OFD 是把 XML 里的 TextCode 拼成同样的
    一串文本后喂给这里 —— 两条路共用一套正则，避免维护两份解析逻辑。
    """

    m = re.search(r'发票号码\s*[:：]?\s*(\d+)', text)
    inv_no = m.group(1) if m else ''
    if not inv_no:
        m = re.search(r'(\d{20})', name)
        inv_no = m.group(1) if m else ''

    buyer, seller = party_names(text)

    m = re.search(r'开票日期\s*[:：]?\s*([^\r\n]+)', text)
    raw_date = m.group(1).strip() if m else ''
    iso = ''
    md = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', raw_date)
    if md:
        iso = f'{md.group(1)}-{int(md.group(2)):02d}-{int(md.group(3)):02d}'

    amount = extract_amount(text)
    override = amount_overrides().get(name)
    if isinstance(override, (int, float)):
        amount = round(float(override), 2)

    up_val = extract_upper(text)

    m = re.search(r'\*([^*\r\n]+)\*([^\r\n]+)', text)
    item = f'{m.group(1).strip()}·{m.group(2).strip()}' if m else ''

    m = re.search(r'税\s*额[^\d]*([\d,]+\.\d{2})', text)
    tax = float(m.group(1).replace(',', '')) if m else None

    m = re.search(r'^\s*(?:电子发票|增值税)[^\r\n]*', text, re.M)
    inv_type = m.group(0).strip() if m else '电子发票'

    # 金额校验：小写 vs 大写
    #   True  = 一致
    #   False = 不一致（标红待确认）
    #   None  = 无法判定（缺大写或小写）
    ok = None
    if amount is not None and up_val is not None:
        ok = abs(amount - up_val) < 0.005

    return {
        'invoice_no': inv_no,
        'seller': seller,
        'buyer': buyer,
        'issue_date': iso,
        'amount': amount,
        'amount_upper': up_val,
        'amount_ok': ok,
        'tax': tax,
        'item': item,
        'inv_type': inv_type,
        'raw_upper': '',
    }


def parse_pdf(path):
    return parse_fields(pdf_text(path), os.path.basename(path))


def ofd_text(path):
    """把 .ofd（本质是 zip + xml）里的文字节点拼成纯文本。"""
    import safezip
    parts = []
    with zipfile.ZipFile(path) as zf:
        bad = safezip.check(zf)
        if bad:
            raise ValueError(bad)
        for n in zf.namelist():
            if not n.lower().endswith('.xml'):
                continue
            try:
                raw = safezip.entry(zf, n, safezip.MAX_ENTRY)
                root = ET.fromstring(raw)
            except (ET.ParseError, ValueError, OSError):
                continue
            for el in root.iter():
                if el.tag.split('}')[-1] in ('TextCode', 'Text'):
                    if el.text and el.text.strip():
                        parts.append(el.text.strip())
    return ' '.join(parts)


def _parse_rail_text(text, name):
    """铁路电子客票（OFD 版）：金额字段叫「票价」，不是「价税合计」。"""
    m = re.search(r'票价\s*[:：]?\s*[¥￥]\s*([\d,]+(?:\.\d{1,2})?)', text)
    fare = float(m.group(1).replace(',', '')) if m else None

    m = re.search(r'发票号码\s*[:：]?\s*(\d+)', text)
    inv_no = m.group(1) if m else ''

    m = re.search(r'开票日期\s*[:：]?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})', text)
    iso = f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}' if m else ''

    m = re.search(r'\b([GDCZTK]\d{1,4})\b', text)
    train = m.group(1) if m else ''

    m = re.search(r'购买方名称\s*[:：]?\s*([^\s]{2,40})', text)
    buyer = m.group(1) if m else ''

    cities = re.findall(r'([\u4e00-\u9fa5]{2,6})\s+[A-Za-z]{3,}', text)
    route = ' → '.join(cities[:2]) if len(cities) >= 2 else ''

    return {
        'invoice_no': inv_no or os.path.basename(path)[:-4],
        'seller': f'中国铁路 {train}次' if train else '中国铁路',
        'buyer': buyer,
        'issue_date': iso,
        'amount': fare,
        'amount_upper': fare,
        'amount_ok': True if fare is not None else None,
        'tax': None,
        'item': f'铁路电子客票 {train} {route}'.strip(),
        'inv_type': '铁路电子客票',
        'raw_upper': '',
    }


def parse_ofd(path):
    """独立 .ofd 发票：抽文字后按同一套正则解析。

    OFD 里的铁路票金额字段是「票价」，先走专用分支，其余走通用字段解析。
    """
    text = ofd_text(path)
    if '铁路电子客票' in text or '票价' in text:
        return _parse_rail_text(text, os.path.basename(path))
    return parse_fields(text, os.path.basename(path))


def parse_train_zip(path):
    fare = 0.0
    train_no = tdate = dep = dest = ticket_no = seat = buyer = ''
    import safezip
    with zipfile.ZipFile(path) as zf:
        bad = safezip.check(zf)
        if bad:
            raise ValueError(bad)
        for n in zf.namelist():
            if n.endswith('.ofd'):
                blob = safezip.entry(zf, n)
                if blob is None:
                    raise ValueError('压缩包内文件过大')
                with zipfile.ZipFile(io.BytesIO(blob)) as oz:
                    bad = safezip.check(oz)
                    if bad:
                        raise ValueError(bad)
                    for fn in oz.namelist():
                        if 'rai_issuer' in fn:
                            data = safezip.entry(oz, fn)
                            if data is None:
                                raise ValueError('压缩包内文件过大')
                            root = ET.fromstring(data)
                            for e in root.iter():
                                tag = e.tag.split('}')[-1]
                                t = (e.text or '').strip()
                                if tag == 'Fare':
                                    fare = float(t)
                                elif tag == 'TrainNumber':
                                    train_no = t
                                elif tag == 'TravelDate':
                                    tdate = t
                                elif tag == 'DepartureStation':
                                    dep = t
                                elif tag == 'DestinationStation':
                                    dest = t
                                elif tag == 'ElectronicInvoiceRailwayETicketNumber':
                                    ticket_no = t
                                elif tag == 'SeatLevel':
                                    seat = t
                                elif tag in ('BuyerName', 'PurchaserName'):
                                    buyer = t
    fare_missing = not fare
    return {
        'invoice_no': ticket_no or os.path.basename(path)[:-4],
        'seller': f'中国铁路 {train_no}次',
        'buyer': buyer,
        'issue_date': tdate,
        'amount': (None if fare_missing else fare),
        'amount_upper': (None if fare_missing else fare),
        'amount_ok': (None if fare_missing else True),
        'tax': None,
        'item': f'{dep} → {dest}（{seat}）',
        'inv_type': '铁路电子客票',
        'raw_upper': '',
        'train': {'no': train_no, 'from': dep, 'to': dest, 'date': tdate, 'seat': seat},
    }


def scan_dir(root, tag):
    items = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
        for fn in filenames:
            if fn in SKIP_NAMES or fn.startswith('.'):
                continue
            low = fn.lower()
            if not low.endswith(WHITELIST):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            rec = {
                'filename': fn,
                'rel_path': rel,
                'size': os.path.getsize(full),
                'hash': file_hash(full),
                'source': tag,
                'status': 'normal',
                'category': None,
                'ai_suspect': False,
            }
            try:
                if low.endswith('.pdf'):
                    rec.update(parse_pdf(full))
                elif low.endswith('.ofd'):
                    # 独立 OFD 发票 —— 以前一律落到「图片/其他」，金额永远不进合计
                    rec.update(parse_ofd(full))
                elif low.endswith('.zip'):
                    # 只把铁路票 zip 当票；其它容器包跳过
                    with zipfile.ZipFile(full) as zf:
                        names = zf.namelist()
                    has_ofd = any(n.endswith('.ofd') for n in names)
                    has_pdf = any(n.lower().endswith('.pdf') for n in names)
                    if has_ofd:
                        rec.update(parse_train_zip(full))
                    elif has_pdf:
                        rec.update({'inv_type': '容器包', 'amount': None,
                                    'invoice_no': '', 'seller': '（内含 PDF，不单独计账）',
                                    'buyer': '', 'issue_date': '', 'item': '',
                                    'amount_upper': None, 'amount_ok': None, 'tax': None,
                                    'raw_upper': ''})
                        rec['status'] = 'container'
                    else:
                        continue
                else:
                    rec.update({'inv_type': '图片/其他', 'amount': None, 'invoice_no': '',
                                'seller': '', 'buyer': '', 'issue_date': '', 'item': '',
                                'amount_upper': None, 'amount_ok': None, 'tax': None,
                                'raw_upper': ''})
                    rec['status'] = 'unverified'
            except Exception as e:
                rec.update({'inv_type': '解析失败', 'amount': None, 'invoice_no': '',
                            'seller': str(e)[:60], 'buyer': '', 'issue_date': '', 'item': '',
                            'amount_upper': None, 'amount_ok': None, 'tax': None,
                            'raw_upper': ''})
                rec['status'] = 'error'
            items.append(rec)
    return items


COUNTED_SKIP = ('dup', 'container', 'error')


def is_counted(it):
    """该条目是否计入合计 —— 全项目唯一判定。

    扫描统计、/api/summary、Excel 导出、前端小计都必须调这个函数；
    四处各写一份「排除 dup/container」，迟早算出四个不同的总额。
    """
    return it.get('status') not in COUNTED_SKIP and it.get('amount') is not None


def main():
    print('扫描根目录（含子目录）…')
    all_items = scan_dir(ROOT, 'root')
    # 标记哪些来自 111 子目录
    for it in all_items:
        if it['rel_path'].replace('\\', '/').startswith('111/'):
            it['source'] = '111'

    # 去重：按发票号，其次按哈希
    seen_no, seen_hash = {}, {}
    for it in all_items:
        no = it.get('invoice_no') or ''
        h = it['hash']
        if no and no in seen_no:
            it['status'] = 'dup'
            it['dup_of'] = seen_no[no]['filename']
        elif h in seen_hash:
            it['status'] = 'dup'
            it['dup_of'] = seen_hash[h]['filename']
        else:
            if no:
                seen_no[no] = it
            seen_hash[h] = it

        # 金额校验不通过 → 待确认。
        # 必须先排除重复票：否则 dup 会被覆盖成 unverified，
        # 同一张票就重复计账（实测 351 → 1998）。
        if it['status'] == 'dup':
            pass
        elif it.get('amount_ok') is False:
            it['status'] = 'unverified'

        # 金额没读出来（但仍是发票）→ 也必须标「待确认」，
        # 否则会出现“金额显示 —、状态却是正常”的误导情况
        if it['status'] == 'normal' and it.get('amount') is None:
            it['status'] = 'unverified'
            it['unverified_reason'] = 'amount_missing'
        elif it['status'] == 'normal' and it.get('amount_ok') is None:
            it['unverified_reason'] = 'no_upper_check'

    # 容器包/重复票/解析失败不计账，统一由 is_counted() 判定
    total = sum(it['amount'] for it in all_items if is_counted(it))
    n_count = sum(1 for it in all_items if is_counted(it))
    n_dup = sum(1 for it in all_items if it['status'] == 'dup')
    n_bad = sum(1 for it in all_items if it['status'] in ('unverified', 'error'))

    print(f'共 {len(all_items)} 个文件，计账 {n_count} 张，合计 {total:.2f} 元，重复 {n_dup}，异常 {n_bad}')

    payload = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'root': ROOT,
        'items': all_items,
        'summary': {
            'files': len(all_items),
            'counted': n_count,
            'duplicates': n_dup,
            'abnormal': n_bad,
            'total': round(total, 2),
        },
    }
    out = out_path()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)      # 原子替换：中断不会留下半截 JSON
    print('已写出', out)


if __name__ == '__main__':
    main()
