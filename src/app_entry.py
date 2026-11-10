"""打包入口（PyInstaller / pythonw）—— 只负责确定目录，HTTP 逻辑全部复用 serve_api。

历史教训：这里曾经另写了一份 Handler，结果立刻和 serve_api 分叉——
漏了 PATCH（分类重命名/折叠 501）、没调 api.bind_root()（冻结版从 _MEIPASS
读数据、扫描却写 exe 目录，于是每次启动都是 0 张票）。两套入口重复维护
必然再次分叉，所以现在这里只剩三件事：资源目录、工作目录、交给 serve_api。
"""
import os
import sys
import time


def _trace(msg):
    """启动排障：把关键阶段追加到 exe 同目录的 startup.log。

    打包版在用户机器上出问题时看不到控制台输出，这个文件是唯一的现场证据。
    每条只有一行，正常启动也就十几行，不会长大。
    """
    try:
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, 'startup.log'), 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:                                   # noqa: BLE001
        pass


def main():
    _trace('1 enter main')
    import paths

    _trace('2 paths ok')
    base = paths.resource_dir()
    _trace('3 resource_dir=%s' % base)
    sys.path.insert(0, paths.app_dir())

    exe_dir = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
               else paths.resource_dir())
    _trace('4 exe_dir=%s' % exe_dir)

    import serve_api

    _trace('5 serve_api ok')

    # 打包版里 app_dir() 指向临时解包目录，向上探测发票目录没有意义，
    # 直接把「exe 所在目录」作为默认根；用户已有配置则以配置为准。
    if getattr(sys, 'frozen', False) and not os.environ.get('INVOICE_ROOT'):
        os.environ['INVOICE_ROOT'] = exe_dir

    serve_api.WEB_DIR = os.path.join(base, 'web')
    _trace('6 web_dir=%s' % serve_api.WEB_DIR)
    serve_api.main()
    _trace('7 serve_api.main returned')


if __name__ == '__main__':
    main()
