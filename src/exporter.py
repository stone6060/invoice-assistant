"""发票助手 · Excel 导出

关键点（踩过的坑）：
  openpyxl 写公式时只写 <f> 不写 <v>，导致 WPS / 预览器打开时
  总金额单元格显示空白。这里在保存后回填缓存值，并标记 fullCalcOnLoad。
"""
import os
import re
import zipfile
import io
from datetime import datetime

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from scan_demo_data import is_counted

# ---------------------------------------------------------------- 样式
F_TITLE = Font(name='微软雅黑', size=15, bold=True, color='1F497D')
F_SUB = Font(name='微软雅黑', size=9, color='595959')
F_HEAD = Font(name='微软雅黑', size=10, bold=True, color='FFFFFF')
F_DATA = Font(name='微软雅黑', size=9)
F_BOLD = Font(name='微软雅黑', size=9, bold=True)
F_TOTAL = Font(name='微软雅黑', size=10, bold=True, color='C00000')
F_BANNER = Font(name='微软雅黑', size=18, bold=True, color='C00000')

FILL_HEAD = PatternFill('solid', start_color='2A4B7C', end_color='2A4B7C')
FILL_SUB = PatternFill('solid', start_color='DCE6F1', end_color='DCE6F1')
FILL_TOT = PatternFill('solid', start_color='F2DCDB', end_color='F2DCDB')
FILL_ALT = PatternFill('solid', start_color='F9FAFB', end_color='F9FAFB')
FILL_BAN = PatternFill('solid', start_color='FFF2CC', end_color='FFF2CC')

TH = Side(border_style='thin', color='D9D9D9')
TOP = Side(border_style='thin', color='000000')
DBL = Side(border_style='double', color='000000')
BOX = Border(left=TH, right=TH, top=TH, bottom=TH)
SUB_B = Border(left=TH, right=TH, top=TOP, bottom=TH)
TOT_B = Border(left=TH, right=TH, top=TOP, bottom=DBL)

C = Alignment(horizontal='center', vertical='center')
L = Alignment(horizontal='left', vertical='center')
R = Alignment(horizontal='right', vertical='center')
MONEY = '¥#,##0.00'


_FORMULA_LEAD = ('=', '+', '-', '@', chr(9), chr(13))


def _cell_safe(v):
    """把可能被 Excel 当成公式的文本转成普通文本。

    销售方、商品名都是 PDF 里的自由文本。若原样写进单元格，一条内容为
    `=cmd|' /C calc'!'!A0'` 的"销售方"就变成 DDE 载荷；`=HYPERLINK(...)`
    还能在用户点开时把金额带出去。
    """
    if isinstance(v, str) and v[:1] in _FORMULA_LEAD:
        return "'" + v
    return v


def _row_safe(row):
    return [_cell_safe(v) for v in row]


def _cached_values(wb, cache):
    """把公式结果回填进 xlsx 内部的 <v>，解决预览空白问题。"""
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    with zipfile.ZipFile(buf) as z:
        names = z.namelist()
        content = {n: z.read(n) for n in names}

    wbxml = content['xl/workbook.xml'].decode('utf-8')
    rels = content['xl/_rels/workbook.xml.rels'].decode('utf-8')

    rid2t = {}
    for m in re.finditer(r'<Relationship\b[^>]*>', rels):
        tag = m.group(0)
        rid = re.search(r'Id="([^"]+)"', tag)
        tgt = re.search(r'Target="([^"]+)"', tag)
        if rid and tgt:
            t = tgt.group(1).lstrip('/')
            if not t.startswith('xl/'):
                t = 'xl/' + t
            rid2t[rid.group(1)] = t

    title2file = {}
    for m in re.finditer(r'<sheet\b[^>]*/>', wbxml):
        tag = m.group(0)
        nm = re.search(r'name="([^"]+)"', tag)
        rid = re.search(r'r:id="([^"]+)"', tag)
        if nm and rid and rid.group(1) in rid2t:
            title = (nm.group(1).replace('&amp;', '&')
                     .replace('&lt;', '<').replace('&gt;', '>'))
            title2file[title] = rid2t[rid.group(1)]

    for title, cells in cache.items():
        f = title2file.get(title)
        if not f or f not in content:
            continue
        xml = content[f].decode('utf-8')
        for ref, val in cells.items():
            num = f'{float(val):.10g}'
            pat = re.compile(r'(<c r="%s"[^>]*?)(/>|>(.*?)</c>)' % re.escape(ref), re.S)
            m = pat.search(xml)
            if not m:
                continue
            if m.group(2) == '/>':
                new = m.group(1) + f'><v>{num}</v></c>'
            else:
                inner = m.group(3)
                new_inner = re.sub(
                    r'<f>.*?</f>\s*(?:<v>\s*</v>)?',
                    lambda mm: mm.group(0).split('</f>')[0] + '</f>' + f'<v>{num}</v>',
                    inner, count=1, flags=re.S)
                if new_inner == inner:
                    new_inner = inner + f'<v>{num}</v>'
                new = m.group(1) + '>' + new_inner + '</c>'
            xml = xml[:m.start()] + new + xml[m.end():]
        content[f] = xml.encode('utf-8')

    # 打开时强制重算一次
    if '<calcPr' in wbxml:
        wbxml = re.sub(r'<calcPr\b[^>]*/>',
                       '<calcPr calcId="191029" fullCalcOnLoad="1"/>', wbxml)
    else:
        wbxml = wbxml.replace('</workbook>',
                              '<calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
    content['xl/workbook.xml'] = wbxml.encode('utf-8')

    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.writestr(n, content[n])
    out.seek(0)
    return out


def export_excel(items, out_path, scope_label='全部发票'):
    """生成 Excel。返回统计信息。"""
    wb = openpyxl.Workbook()
    cache = {}

    counted = [i for i in items if is_counted(i)]
    total = round(sum(i['amount'] for i in counted), 2)

    # 按分类分组（优先用前端传的分类名）
    groups = {}
    for i in items:
        key = i.get('category_name') or '待归类'
        groups.setdefault(key, []).append(i)

    # ==================== Sheet1 汇总 ====================
    ws = wb.active
    ws.title = '发票汇总'

    ws['A1'] = f'发票统计表（{scope_label}）'
    ws['A1'].font = F_TITLE
    ws['A1'].alignment = L
    ws.row_dimensions[1].height = 28

    ws['A2'] = f'导出时间 {datetime.now():%Y-%m-%d %H:%M} ｜ 文件 {len(items)} 个 ｜ 计账 {len(counted)} 张'
    ws['A2'].font = F_SUB
    ws['A2'].alignment = L

    ws['A3'] = '总金额'
    ws['A3'].font = Font(name='微软雅黑', size=12, bold=True)
    ws['A3'].alignment = L
    ws['A3'].fill = FILL_BAN

    headers = ['序号', '发票名字', '金额（元）', '发票号码', '销售方', '开票日期', '项目/商品', '类型', '分类', '状态']
    ws.append([])
    ws.append(headers)
    ws.row_dimensions[5].height = 24
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=5, column=c)
        cell.font = F_HEAD
        cell.fill = FILL_HEAD
        cell.alignment = C
        cell.border = BOX

    cur = 6
    idx = 0
    for it in items:
        idx += 1
        cat_label = it.get('category_name') or '待归类'
        row = [
            idx,
            it.get('filename', ''),
            it.get('amount'),
            it.get('invoice_no', ''),
            it.get('seller', ''),
            it.get('issue_date', ''),
            it.get('item', ''),
            it.get('inv_type', ''),
            cat_label,
            it.get('status', ''),
        ]
        ws.append(_row_safe(row))
        ws.row_dimensions[cur].height = 20
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=cur, column=c)
            cell.font = F_DATA
            cell.border = BOX
            if c in (1, 6, 8, 10):
                cell.alignment = C
            elif c == 3:
                cell.alignment = R
                cell.number_format = MONEY
            else:
                cell.alignment = L
            if idx % 2 == 0:
                cell.fill = FILL_ALT
        cur += 1

    data_end = cur - 1

    # 总金额条（引用汇总，带缓存）
    # 按状态列（J）求和：排除重复票、容器包、解析失败 —— 与后端 is_counted() 保持一致。
    # 早先误写成 SUMIF(分类列,"<>容器包")，条件恒真，Excel 一重算就比真实总额大。
    ws['C3'] = (f'=SUMIFS(C6:C{data_end},J6:J{data_end},"<>dup",'
                f'J6:J{data_end},"<>container",J6:J{data_end},"<>error")')
    ws['C3'].font = F_BANNER
    ws['C3'].alignment = R
    ws['C3'].number_format = MONEY
    ws['C3'].fill = FILL_BAN
    cache['发票汇总'] = {'C3': total}

    widths = [6, 44, 14, 22, 30, 12, 26, 16, 14, 10]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A6'

    # ==================== Sheet2 分类小计 ====================
    ws2 = wb.create_sheet('分类小计')
    ws2['A1'] = '分类小计'
    ws2['A1'].font = F_TITLE
    ws2['A1'].alignment = L
    ws2.row_dimensions[1].height = 26

    h2 = ['分类', '张数', '金额（元）', '占比']
    ws2.append([])
    ws2.append(h2)
    ws2.row_dimensions[3].height = 22
    for c in range(1, len(h2) + 1):
        cell = ws2.cell(row=3, column=c)
        cell.font = F_HEAD
        cell.fill = FILL_HEAD
        cell.alignment = C
        cell.border = BOX

    r2 = 4
    order = sorted(
        groups.items(),
        key=lambda kv: (kv[0] == '待归类',
                        -sum(i['amount'] for i in kv[1] if i.get('amount') is not None)))
    for key, arr in order:
        cnt = [i for i in arr if is_counted(i)]
        amt = round(sum(i['amount'] for i in cnt), 2)
        ws2.append([_cell_safe(key), len(cnt), amt, (amt / total) if total else 0])
        ws2.row_dimensions[r2].height = 20
        for c in range(1, 5):
            cell = ws2.cell(row=r2, column=c)
            cell.font = F_DATA
            cell.border = BOX
            cell.alignment = C if c in (1, 2) else R
            if c == 3:
                cell.number_format = MONEY
            elif c == 4:
                cell.number_format = '0.0%'
        r2 += 1

    sub_end = r2 - 1
    ws2.append(['合计', f'=SUM(B4:B{sub_end})', f'=SUM(C4:C{sub_end})', f'=SUM(D4:D{sub_end})'])
    ws2.row_dimensions[r2].height = 22
    for c in range(1, 5):
        cell = ws2.cell(row=r2, column=c)
        cell.font = F_TOTAL
        cell.fill = FILL_TOT
        cell.border = TOT_B
        cell.alignment = C if c in (1, 2) else R
        if c == 3:
            cell.number_format = MONEY
        elif c == 4:
            cell.number_format = '0.0%'
    cache['分类小计'] = {f'B{r2}': len(counted), f'C{r2}': total, f'D{r2}': (1.0 if total else 0)}

    for i, w in enumerate([22, 10, 16, 10], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # ==================== Sheet3 异常清单 ====================
    ws3 = wb.create_sheet('异常清单')
    ws3['A1'] = '异常与不计账清单'
    ws3['A1'].font = F_TITLE
    ws3['A1'].alignment = L
    ws3.row_dimensions[1].height = 26

    h3 = ['发票名字', '金额（元）', '状态', '原因']
    ws3.append([])
    ws3.append(h3)
    ws3.row_dimensions[3].height = 22
    for c in range(1, len(h3) + 1):
        cell = ws3.cell(row=3, column=c)
        cell.font = F_HEAD
        cell.fill = FILL_HEAD
        cell.alignment = C
        cell.border = BOX

    REASON = {
        'dup': '与已有发票重复（同号或同哈希），不重复计账',
        'container': '容器压缩包，内含发票已单独计入',
        'unverified': '金额校验未通过或未能读出，需人工确认',
        'error': '解析失败',
    }
    r3 = 4
    abn = [i for i in items if i.get('status') in ('dup', 'container', 'unverified', 'error')]
    for it in abn:
        reason = it.get('unverified_reason') or REASON.get(it.get('status'), '')
        ws3.append(_row_safe([it.get('filename', ''), it.get('amount'),
                              it.get('status', ''), reason]))
        ws3.row_dimensions[r3].height = 20
        for c in range(1, 5):
            cell = ws3.cell(row=r3, column=c)
            cell.font = F_DATA
            cell.border = BOX
            cell.alignment = C if c == 3 else (R if c == 2 else L)
            if c == 2:
                cell.number_format = MONEY
        r3 += 1

    if not abn:
        ws3.append(['（无）', None, '', ''])
        for c in range(1, 5):
            cell = ws3.cell(row=4, column=c)
            cell.font = F_DATA
            cell.border = BOX
            cell.alignment = C

    for i, w in enumerate([46, 14, 14, 46], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    # ---- 保存并回填缓存 ----
    data = _cached_values(wb, cache)
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(data.read())

    return {
        'path': out_path,
        'files': len(items),
        'counted': len(counted),
        'total': total,
        'groups': len(groups),
        'abnormal': len(abn),
    }
