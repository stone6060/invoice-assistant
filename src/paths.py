"""路径与外部工具解析 —— 保证在任何机器上都能跑，不依赖固定盘符。

解析顺序：
  · 扫描根目录  : 环境变量 INVOICE_ROOT > .发票助手/config.json 的 scan.root > 自动探测
  · pdftotext   : 环境变量 PDFTOTEXT > PATH 查找 > 常见安装路径
"""
import os
import shutil
import sys


def app_dir():
    """程序自身所在目录（即含本文件的那一层）。"""
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    """只读资源（web/、模板等）所在目录。

    源码运行：src/ 的上一级（仓库根）——因为 web/ 放在仓库根；
    打包运行：PyInstaller 的 _MEIPASS。
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    here = app_dir()
    parent = os.path.dirname(here)
    if os.path.isdir(os.path.join(parent, 'web')):
        return parent
    return here


_INVOICE_HINT = None      # 延迟编译，避免 import 期的开销


def _count_invoices(path):
    """统计目录一层里「像发票的文件数 / 普通文件总数」（只看当前层）。

    只数 .pdf/.zip 不够——项目素材目录里常常也躺着几个无关的 pdf（竞赛规则、
    获奖证书、打包产物），会被误判成发票目录。这里额外要求文件名带发票特征
    （dzfp / ostb / invoice / 发票 / fapiao），真实发票几乎都满足。
    """
    import re
    global _INVOICE_HINT
    if _INVOICE_HINT is None:
        _INVOICE_HINT = re.compile(r'dzfp|ostb|invoice|发票|fapiao|\d{15,}', re.I)
    try:
        names = os.listdir(path)
    except OSError:
        return 0, 0
    total = inv = 0
    for n in names:
        if os.path.isdir(os.path.join(path, n)):
            continue
        total += 1
        if n.lower().endswith(('.pdf', '.ofd', '.zip')) and _INVOICE_HINT.search(n):
            inv += 1
    return inv, total


def _walk_up_for_invoices(start, max_level=4, threshold=3):
    """从 start 往上找发票目录。

    判据是「数量 + 占比」：至少 threshold 个文件名像发票的文件，且占该层
    普通文件的 30% 以上。两个条件缺一不可，否则上级素材目录（几个竞赛
    规则 pdf 混在项目文件里）会被误判成发票目录。
    """
    cur = start
    for _ in range(max_level):
        inv, total = _count_invoices(cur)
        if inv >= threshold and total and inv / total >= 0.3:
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def resolve_root(config_root=None):
    """确定扫描根目录。

    1. 环境变量 INVOICE_ROOT（最高优先级，便于脚本化/迁移）
    2. config.json 里已保存且仍存在的路径
    3. 程序目录本身 / 其上级目录中含发票的那一个
    4. 兜底：程序目录的上级（发票文件夹与程序同一层时的常见布局）
    """
    env = os.environ.get('INVOICE_ROOT', '').strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env)

    if config_root and os.path.isdir(config_root):
        return os.path.abspath(config_root)

    here = app_dir()
    found = _walk_up_for_invoices(here)
    if found:
        return found

    return os.path.dirname(here)


_pdf_backend_ok = None      # 探测结果缓存（None = 未探测）


def _can_extract_chinese(exe):
    """探测该 pdftotext 能否正确输出中文。

    思路：用系统里任意一个含中文的 PDF 做实测；没有样本时退回版本信息启发式。
    某些精简构建（例如只带 Latin 编码表的版本）会静默吞掉中文——退出码仍是 0，
    但对发票解析等于不可用，必须排除。

    verify_sample: 供调用方注入一个已知可用的 PDF 路径（可选）。
    """
    import subprocess

    sample = os.environ.get('PDFTOTEXT_TEST_SAMPLE', '').strip()
    if sample and os.path.isfile(sample):
        try:
            r = subprocess.run([exe, sample, '-'], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=25)
            out = r.stdout.decode('utf-8', errors='ignore')
            # 含中文且能读出常见发票字段，才算通过
            has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in out)
            return has_cjk and len(out.strip()) > 20
        except Exception:
            return False

    # 无样本：看构建信息。poppler 版支持 CJK 映射；xpdf 4.x 精简版常吞中文。
    try:
        r = subprocess.run([exe, '-v'], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=15)
        info = (r.stdout or b'').decode('utf-8', errors='ignore').lower()
        if not info.strip():
            return False
        if 'xpdfreader' in info:
            return False          # xpdf 构建：中文映射不可靠，排除
        return 'poppler' in info
    except Exception:
        return False


def find_pdftotext(verify=True):
    """定位 pdftotext 可执行文件，找不到或不可用返回 None（调用方降级处理）。

    verify=True 时会对候选逐个做中文输出实测，跳过不可用的版本。
    """
    global _pdf_backend_ok

    env = os.environ.get('PDFTOTEXT', '').strip()
    if env and os.path.isfile(env):
        return env

    cands = []

    if sys.platform.startswith('win'):
        cands += [
            r'C:\Program Files\poppler\Library\bin\pdftotext.exe',
            r'C:\Program Files (x86)\poppler\Library\bin\pdftotext.exe',
            r'C:\poppler\Library\bin\pdftotext.exe',
        ]
        for base in ('C:\\MiKTeX', 'D:\\MiKTeX', 'E:\\MiKTeX', 'F:\\MiKTeX'):
            cands.append(os.path.join(base, 'miktex', 'bin', 'x64', 'pdftotext.EXE'))
    else:
        cands += ['/usr/bin/pdftotext', '/usr/local/bin/pdftotext', '/opt/homebrew/bin/pdftotext']

    # PATH 里的兜底候选放最后：可能被别的软件目录抢先（如只带英文表的 xpdf 构建）
    for name in ('pdftotext', 'pdftotext.exe'):
        hit = shutil.which(name)
        if hit:
            cands.append(hit)

    seen = set()
    cands = [c for c in cands if os.path.isfile(c) and not (c in seen or seen.add(c))]

    if not verify:
        return cands[0] if cands else None

    if _pdf_backend_ok is None:
        _pdf_backend_ok = ''
        for c in cands:
            if _can_extract_chinese(c):
                _pdf_backend_ok = c
                break
    return _pdf_backend_ok or None


