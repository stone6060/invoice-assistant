"""发票助手 · 本地服务（精简版）

只做三件事：
  · 提供网页界面
  · 提供本机 API（回环地址 + 令牌）
  · 页面关闭后自动退出

MCP 走独立的 stdio 进程（mcp_server.py），不占用这里。
"""
import os
import sys
import json
import socket
import threading
import time
import webbrowser
import urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api_service as api
import paths

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
IDLE_TIMEOUT = 60
MAX_BODY = 1024 * 1024          # 1 MB，本服务的请求体只是小 JSON
_last_beat = time.time()

# 只接受用回环地址访问的请求。少了这一条，任何网页都能靠 DNS rebinding
# 把自己的域名解析到 127.0.0.1，然后用同源请求把令牌和发票全读走。
_ALLOWED_HOSTS = ('127.0.0.1', 'localhost', '[::1]')


def host_allowed(headers, port):
    host = (headers.get('Host') or '').strip().lower()
    if not host or not port:
        return False
    return host in {f'{h}:{port}' for h in _ALLOWED_HOSTS}


def pick_port(preferred=17800):
    """找一个能独占绑定的端口。

    注意：不能图省事设 SO_REUSEADDR —— Windows 下它允许两个进程绑同一个端口
    （实测两个 serve_api 同时 LISTEN 17802），请求会被旧实例接走，
    表现为「改了代码没生效」或「令牌明明对却 401」。
    """
    for p in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    raise RuntimeError('no free port')


class Server(ThreadingHTTPServer):
    """禁用地址复用，防止同端口被两个进程同时监听。"""
    allow_reuse_address = False
    daemon_threads = True


class Handler(SimpleHTTPRequestHandler):
    cfg = None

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return None
        if n > MAX_BODY:
            # 不读、不缓存，直接断开：ThreadingHTTPServer 一连接一线程，
            # 放任一个声明 5 GB 的连接就能把线程池占死。
            self.close_connection = True
            return None
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return None

    def _handle_api(self, method):
        global _last_beat
        _last_beat = time.time()

        u = urlparse(self.path)
        # 中文文件名是 URL 编码的，必须解码后才能匹配
        path = urllib.parse.unquote(u.path)
        query = {k: v[0] for k, v in parse_qs(u.query).items()}

        port = (Handler.cfg or {}).get('api', {}).get('port')
        if not host_allowed(self.headers, port):
            self._json({'error': 'forbidden',
                        'hint': '只接受 127.0.0.1 / localhost 访问'}, 403)
            return

        if not api.check_token(Handler.cfg, self.headers):
            # 网页自身的引导接口免令牌（只返回令牌与端口，不含发票数据）。
            # 它位于 Host 校验之后，所以 rebinding 拿不到。
            if path != '/api/bootstrap':
                self._json({'error': 'unauthorized',
                            'hint': '需要 X-Api-Token 请求头'}, 401)
                return

        handler, params = api.match_route(method, path)
        if not handler:
            self._json({'error': 'not found', 'path': path}, 404)
            return

        body = self._read_body() if method in ('POST', 'PUT', 'PATCH') else None
        try:
            self._json(handler(Handler.cfg, query, body, params))
        except Exception as e:
            # 细节只进 stderr：绝对路径、内部异常文本都不该回给页面
            print(f'[api] {method} {path} 失败: {type(e).__name__}: {e}',
                  file=sys.stderr)
            self._json({'error': 'internal error'}, 500)

    def do_GET(self):
        global _last_beat
        if self.path.startswith('/api/'):
            self._handle_api('GET')
            return
        _last_beat = time.time()
        super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/'):
            self._handle_api('POST')
            return
        self._json({'error': 'not found'}, 404)

    def do_PATCH(self):
        if self.path.startswith('/api/'):
            self._handle_api('PATCH')
            return
        self._json({'error': 'not found'}, 404)

    def do_DELETE(self):
        if self.path.startswith('/api/'):
            self._handle_api('DELETE')
            return
        self._json({'error': 'not found'}, 404)


def watchdog(server):
    while True:
        time.sleep(2)
        if time.time() - _last_beat > IDLE_TIMEOUT:
            print('\n页面已关闭，服务自动退出。')
            server.shutdown()
            break


def _migrate_legacy_data():
    """把旧的 web/demo-data.js 搬出静态目录。

    web/ 是免鉴权的静态目录：发票数据放在那里，等于令牌形同虚设——
    任何本机进程 `curl /demo-data.js` 就能绕过鉴权拿到全部票据。
    """
    legacy = os.path.join(WEB_DIR, 'demo-data.js')
    if not os.path.exists(legacy):
        return False

    payload = None
    try:
        with open(legacy, encoding='utf-8') as f:
            txt = f.read()
        payload = json.loads(txt[txt.index('=') + 1:].strip().rstrip(';'))
    except Exception:
        payload = None

    if payload and not os.path.exists(api.SCAN_DATA_PATH):
        os.makedirs(api.DATA_DIR, exist_ok=True)
        tmp = api.SCAN_DATA_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, api.SCAN_DATA_PATH)

    try:
        os.replace(legacy, os.path.join(api.DATA_DIR, 'demo-data.js.bak'))
    except OSError:
        try:
            os.remove(legacy)
        except OSError:
            pass
    print('  已把 web/demo-data.js 迁到 .发票助手/（旧版遗留，静态目录不再放数据）')
    return True


def _cli_args(argv):
    """极简参数解析：--root <dir> / --port <n> / --no-browser。"""
    opts = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--root', '--port') and i + 1 < len(argv):
            opts[a.lstrip('-')] = argv[i + 1]
            i += 2
            continue
        if a == '--no-browser':
            opts['no_browser'] = True
        i += 1
    return opts


def main():
    global _last_beat
    opts = _cli_args(sys.argv[1:])
    if opts.get('root'):
        os.environ['INVOICE_ROOT'] = os.path.abspath(opts['root'])

    cfg = api.load_config()

    # 确定扫描根目录
    root_cfg = cfg['scan'].get('root') or ''
    if opts.get('root'):
        cfg['scan']['root'] = os.path.abspath(opts['root'])
        api.save_config(cfg)
    elif (not root_cfg) or (not os.path.isdir(root_cfg)):
        cfg['scan']['root'] = paths.resolve_root()
        api.save_config(cfg)
    api.bind_root(cfg['scan']['root'])      # 数据目录一律跟随扫描根

    _migrate_legacy_data()

    # 首次运行：先扫一遍（只读，不动任何文件），否则页面是空的
    if not os.path.exists(api.SCAN_DATA_PATH):
        print('  首次运行：正在扫描发票目录（只读，不会改动或移动任何文件）…')
        try:
            import scan_demo_data
            scan_demo_data.ROOT = cfg['scan']['root']
            scan_demo_data.DIR111 = os.path.join(cfg['scan']['root'], '111')
            scan_demo_data.main()
        except Exception as e:                      # noqa: BLE001
            print(f'  扫描失败：{type(e).__name__}: {e}')
            print('  可以先启动，再在页面里点「重新扫描」。')

    Handler.cfg = cfg

    port = pick_port(int(opts.get('port') or cfg['api'].get('port') or 17800))
    cfg['api']['port'] = port
    api.save_config(cfg)

    url = f'http://127.0.0.1:{port}/index.html'
    srv = Server(('127.0.0.1', port), Handler)
    threading.Thread(target=watchdog, args=(srv,), daemon=True).start()

    print('=' * 60)
    print('  发票助手 已启动')
    print(f'  网页    {url}')
    print(f'  API     http://127.0.0.1:{port}/api/health')
    print(f'  令牌    {cfg["api"]["token"]}')
    print(f'  发票目录 {cfg["scan"].get("root")}')
    if paths.find_pdftotext() is None:
        print()
        print('  [警告] 未找到可用的 pdftotext，PDF 将无法解析。')
        print('         请安装 poppler 并加入 PATH，或设置环境变量 PDFTOTEXT 指向可执行文件。')
    print()
    print('  MCP 接入（Claude Desktop / Cursor / Cline）:')
    print(f'    command: {sys.executable}')
    print(f'    args:    ["{os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")}"]')
    print('=' * 60)

    _last_beat = time.time()
    if not opts.get('no_browser'):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已手动停止。')
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
