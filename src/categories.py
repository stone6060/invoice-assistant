"""发票助手 · 分类持久化

分类是**全手动**的（工具不猜），所以必须可靠地存下来：
刷新页面、重启服务、换机器迁移，都不应该丢。

存储位置：<发票根目录>/.发票助手/categories.json

数据结构：
{
  "version": 1,
  "updated_at": "2026-11-06T14:20:00",
  "categories": [
    {"id": "cat_0", "name": "电子元件", "color": "deepseek", "collapsed": false}
  ],
  "assignments": {
    "111/_26952000000000000000.pdf": "cat_0"      # rel_path -> category id
  }
}

设计要点：
  · 分类与分配分开存 —— 改分类名不影响分配，删分类只清理分配
  · 原子写入（先写临时文件再 replace）—— 避免中途崩溃写出半个 JSON
  · 分配只记 rel_path，不记绝对路径 —— 换机器/改目录名后依然有效
"""
import os
import sys
import json
import time
import uuid
import contextlib
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FORMAT_VERSION = 1
DEFAULT_COLORS = ['deepseek', 'green', 'amber', 'red', 'violet', 'cyan', 'pink']

MAX_NAME_LEN = 40
_lock = threading.RLock()


def norm_color(color):
    """颜色只允许预设值。

    这个值会被前端拼进 class 名；若原样接受任意字符串，一个 `"><img ...>`
    就能变成持久化 XSS，所以白名单之外一律退回默认色。
    """
    c = (color or '').strip().lower()
    return c if c in DEFAULT_COLORS else None


def norm_name(name):
    """去控制字符并限长，避免把不可打印内容写进分类名。"""
    name = (name or '').strip()
    name = ''.join(ch for ch in name if ch.isprintable())
    return name[:MAX_NAME_LEN]


def _default_data():
    return {'version': FORMAT_VERSION, 'updated_at': None,
            'categories': [], 'assignments': {}}


class _FileLock:
    """跨进程排他锁。

    网页、MCP server、命令行扫描可能同时改分类文件；threading.RLock 只管
    单进程。实测 4 个进程各写 40 次只活下来 7 条，还留下了损坏备份。
    """

    def __init__(self, path):
        self.path = path + '.lock'
        self.fh = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        self.fh = open(self.path, 'a+b')
        if self.fh.seek(0, 2) == 0:
            self.fh.write(b'0')
            self.fh.flush()
        if os.name == 'nt':
            import msvcrt
            self.fh.seek(0)
            while True:
                try:
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.03)
        else:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            if os.name == 'nt':
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None
        return False


class CategoryStore:
    """基于 JSON 文件的分类存储。传 None 可作为纯内存存储（测试用）。"""

    def __init__(self, path):
        self.path = path
        self._mem = None            # path=None 时的内存后备

    # ------------------------------------------------------------ 读写
    def _read(self):
        if self.path is None:
            return json.loads(json.dumps(self._mem or _default_data()))

        if not os.path.exists(self.path):
            return _default_data()
        try:
            with open(self.path, encoding='utf-8') as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            # 文件损坏：不静默丢弃，备份后从空开始，避免反复炸
            try:
                bad = self.path + '.corrupt-' + str(int(time.time()))
                os.replace(self.path, bad)
            except OSError:
                pass
            return _default_data()

        out = _default_data()
        if isinstance(d, dict):
            if isinstance(d.get('categories'), list):
                cats = []
                for c in d['categories']:
                    if not (isinstance(c, dict) and c.get('id')):
                        continue
                    cats.append({
                        'id': str(c['id']),
                        'name': norm_name(c.get('name')) or '未命名',
                        'color': norm_color(c.get('color')) or 'deepseek',
                        'collapsed': bool(c.get('collapsed')),
                    })
                out['categories'] = cats
            if isinstance(d.get('assignments'), dict):
                out['assignments'] = {k: v for k, v in d['assignments'].items()
                                      if isinstance(k, str) and isinstance(v, str)}
            out['updated_at'] = d.get('updated_at')
            out['version'] = d.get('version') or FORMAT_VERSION
        return out

    @contextlib.contextmanager
    def _txn(self):
        """写事务：读-改-写必须在同一把「线程锁 + 文件锁」里完成。"""
        with _lock:
            if self.path is None:
                yield
            else:
                with _FileLock(self.path):
                    yield

    def _write(self, data):
        data['version'] = FORMAT_VERSION
        data['updated_at'] = datetime.now().isoformat(timespec='seconds')
        if self.path is None:
            self._mem = data
            return data

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = '%s.%d.%s.tmp' % (self.path, os.getpid(), uuid.uuid4().hex[:8])
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)      # 原子替换
        return data

    # ------------------------------------------------------------ 查询
    def snapshot(self):
        with _lock:
            d = self._read()
        return {
            'categories': d['categories'],
            'assignments': d['assignments'],
            'updated_at': d['updated_at'],
        }

    # ------------------------------------------------------------ 分类增删改
    def create(self, name, color=None):
        name = norm_name(name)
        color = norm_color(color)
        if not name:
            raise ValueError('分类名不能为空')
        with self._txn():
            d = self._read()
            if any(c.get('name') == name for c in d['categories']):
                raise ValueError('该分类名已存在')
            used = {c.get('color') for c in d['categories']}
            if not color:
                color = next((c for c in DEFAULT_COLORS if c not in used),
                             DEFAULT_COLORS[len(d['categories']) % len(DEFAULT_COLORS)])
            color = color or DEFAULT_COLORS[len(d['categories']) % len(DEFAULT_COLORS)]
            cid = 'cat_%d_%s' % (int(time.time() * 1000) % 10 ** 8,
                                 os.urandom(2).hex())
            cat = {'id': cid, 'name': name, 'color': color, 'collapsed': False}
            d['categories'].append(cat)
            self._write(d)
            return cat

    def rename(self, cat_id, name):
        name = (name or '').strip()
        if not name:
            raise ValueError('分类名不能为空')
        with self._txn():
            d = self._read()
            target = next((c for c in d['categories'] if c['id'] == cat_id), None)
            if not target:
                raise KeyError('分类不存在')
            if any(c['id'] != cat_id and c.get('name') == name for c in d['categories']):
                raise ValueError('该分类名已存在')
            target['name'] = name
            self._write(d)
            return target

    def delete(self, cat_id):
        """删分类，同时把它的分配清空（发票回到待归类）。"""
        with self._txn():
            d = self._read()
            before = len(d['categories'])
            d['categories'] = [c for c in d['categories'] if c['id'] != cat_id]
            if len(d['categories']) == before:
                raise KeyError('分类不存在')
            freed = 0
            for k in [k for k, v in d['assignments'].items() if v == cat_id]:
                del d['assignments'][k]
                freed += 1
            self._write(d)
            return {'deleted': cat_id, 'unassigned': freed}

    def set_collapsed(self, cat_id, collapsed):
        with self._txn():
            d = self._read()
            target = next((c for c in d['categories'] if c['id'] == cat_id), None)
            if not target:
                raise KeyError('分类不存在')
            target['collapsed'] = bool(collapsed)
            self._write(d)
            return target

    # ------------------------------------------------------------ 分配
    def assign(self, rel_paths, cat_id):
        """把多个发票归到某分类；cat_id 传 None 表示移出分类。"""
        if not isinstance(rel_paths, (list, tuple)):
            raise ValueError('rel_paths 必须是数组')
        with self._txn():
            d = self._read()
            if cat_id is not None:
                if not any(c['id'] == cat_id for c in d['categories']):
                    raise KeyError('分类不存在')
            n = 0
            for p in rel_paths:
                if not isinstance(p, str) or not p:
                    continue
                if cat_id is None:
                    if p in d['assignments']:
                        del d['assignments'][p]
                        n += 1
                else:
                    d['assignments'][p] = cat_id
                    n += 1
            self._write(d)
            return {'assigned': n, 'category': cat_id}

    def rekey(self, old_rel, new_rel):
        """文件被移动后，把分配关系跟着换键（否则归档即丢分类）。"""
        if not old_rel or not new_rel or old_rel == new_rel:
            return False
        with self._txn():
            d = self._read()
            if old_rel not in d['assignments']:
                return False
            cat = d['assignments'].pop(old_rel)
            if new_rel in d['assignments']:
                return False
            d['assignments'][new_rel] = cat
            self._write(d)
            return True

    def apply_to_items(self, items):
        """把分配结果叠加到发票列表上（就地修改并返回）。"""
        with _lock:
            d = self._read()
        cats = {c['id'] for c in d['categories']}
        amap = d['assignments']
        for it in items:
            cid = amap.get(it.get('rel_path'))
            # 分类已被删除的悬空分配不生效
            it['category'] = cid if cid in cats else None
        return items

    def stats(self):
        with _lock:
            d = self._read()
        cats = {c['id'] for c in d['categories']}
        counts = {}
        for cid in d['assignments'].values():
            if cid in cats:
                counts[cid] = counts.get(cid, 0) + 1
        return {
            'categories': len(d['categories']),
            'assigned': sum(counts.values()),
            'per_category': counts,
        }
