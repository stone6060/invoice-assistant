"""打包入口（PyInstaller / pythonw）—— 只负责确定目录，HTTP 逻辑全部复用 serve_api。

历史教训：这里曾经另写了一份 Handler，结果立刻和 serve_api 分叉——
漏了 PATCH（分类重命名/折叠 501）、没调 api.bind_root()（冻结版从 _MEIPASS
读数据、扫描却写 exe 目录，于是每次启动都是 0 张票）。两套入口重复维护
必然再次分叉，所以现在这里只剩三件事：资源目录、工作目录、交给 serve_api。
"""
import os
import sys


def main():
    import paths

    base = paths.resource_dir()
    sys.path.insert(0, paths.app_dir())

    exe_dir = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
               else paths.resource_dir())

    import serve_api

    # 打包版里 app_dir() 指向临时解包目录，向上探测发票目录没有意义，
    # 直接把「exe 所在目录」作为默认根；用户已有配置则以配置为准。
    if getattr(sys, 'frozen', False) and not os.environ.get('INVOICE_ROOT'):
        os.environ['INVOICE_ROOT'] = exe_dir

    serve_api.WEB_DIR = os.path.join(base, 'web')
    serve_api.main()


if __name__ == '__main__':
    main()
