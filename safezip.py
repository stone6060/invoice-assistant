"""内存中解压 zip 的体积护栏。

铁路电子客票是 zip → ofd → XBRL，两级压缩都在内存里做，绝不能直接把
压缩包内容读进来：zlib 对全零数据能压到 1000:1，1 MB 的包可以膨胀成 1 GB，
一次「重新扫描」就能把服务 OOM 掉。
"""

MAX_ENTRY = 20 * 1024 * 1024      # 单个条目解压后上限
MAX_TOTAL = 100 * 1024 * 1024     # 整包解压总量上限
MAX_RATIO = 200                   # 压缩比上限


def check(zf):
    """返回拒绝原因；无问题返回 None。"""
    total = 0
    for info in zf.infolist():
        total += info.file_size
        if info.file_size > MAX_ENTRY:
            return f'压缩包内单个文件超过 {MAX_ENTRY // 1024 // 1024} MB'
        if total > MAX_TOTAL:
            return f'压缩包解压总量超过 {MAX_TOTAL // 1024 // 1024} MB'
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            return '压缩比异常（疑似 zip bomb）'
    return None


def entry(zf, name, limit=MAX_ENTRY):
    """流式读取单个条目，最多 limit 字节，超出返回 None。"""
    with zf.open(name) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        return None
    return data
