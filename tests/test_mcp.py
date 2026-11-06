"""MCP Server 协议测试：模拟 Claude Desktop 的完整握手与工具调用。"""
import json
import subprocess
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')

PY = sys.executable
SERVER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src', 'mcp_server.py')


class MCPClient:
    def __init__(self):
        self.p = subprocess.Popen(
            [PY, SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            bufsize=1,
        )
        self._id = 0

    def send(self, method, params=None, notify=False):
        msg = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            msg['params'] = params
        if not notify:
            self._id += 1
            msg['id'] = self._id

        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + '\n')
        self.p.stdin.flush()

        if notify:
            return None
        line = self.p.stdout.readline()
        if not line:
            err = self.p.stderr.read()
            raise RuntimeError(f'no response; stderr={err}')
        return json.loads(line)

    def call(self, name, args=None):
        r = self.send('tools/call', {'name': name, 'arguments': args or {}})
        if 'error' in r:
            return {'error': r['error']}
        content = r.get('result', {}).get('content', [])
        text = content[0]['text'] if content else ''
        return {'text': text, 'isError': r.get('result', {}).get('isError', False)}

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def main():
    c = MCPClient()
    try:
        print('=' * 64)
        print('① initialize 握手')
        r = c.send('initialize', {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'test-client', 'version': '1.0'},
        })
        si = r['result']['serverInfo']
        print(f"   server = {si['name']} v{si['version']}")
        print(f"   protocol = {r['result']['protocolVersion']}")
        print(f"   capabilities = {r['result']['capabilities']}")

        c.send('notifications/initialized', notify=True)

        print()
        print('=' * 64)
        print('② tools/list 工具清单')
        r = c.send('tools/list')
        tools = r['result']['tools']
        print(f'   共 {len(tools)} 个工具：')
        for t in tools:
            req = t['inputSchema'].get('required', [])
            mark = f'  必填: {",".join(req)}' if req else ''
            print(f"     · {t['name']:24} {t['description'][:38]}{mark}")

        print()
        print('=' * 64)
        print('③ invoice_summary')
        print('  ', c.call('invoice_summary')['text'])

        print()
        print('=' * 64)
        print('④ invoice_list (limit=3)')
        out = c.call('invoice_list', {'limit': 3})['text']
        for line in out.splitlines()[:8]:
            print('  ', line)

        print()
        print('=' * 64)
        print('⑤ invoice_list (status=unverified)')
        print('  ', c.call('invoice_list', {'status': 'unverified'})['text'])

        print()
        print('=' * 64)
        print('⑥ invoice_raw_text（胶带票）')
        out = c.call('invoice_raw_text', {
            'key': 'dzfp_26952000000000000001_示例科技有限公司_20260101120000.pdf'
        })['text']
        for line in out.splitlines()[:10]:
            print('  ', line)

        print()
        print('=' * 64)
        print('⑦ invoice_rule_add')
        print('  ', c.call('invoice_rule_add', {
            'match': {'filename_contains': '4282374901'},
            'field': 'amount',
            'value': 1.50,
            'note': 'MCP 测试：按大写补全',
        })['text'])

        print()
        print('=' * 64)
        print('⑧ invoice_rules_list')
        print('  ', c.call('invoice_rules_list')['text'].replace('\n', '\n   '))

        print()
        print('=' * 64)
        print('⑨ 清理测试规则')
        r = c.send('tools/list')
        # 从规则列表里取 id 删除
        rules_text = c.call('invoice_rules_list')['text']
        import re
        for rid in re.findall(r'· (r_[0-9a-f]+)', rules_text):
            print('  ', c.call('invoice_rule_delete', {'id': rid})['text'])

        print()
        print('=' * 64)
        print('⑩ 未知工具应报错')
        r = c.send('tools/call', {'name': 'not_exist', 'arguments': {}})
        print('   error =', r.get('error', {}).get('message'))

    finally:
        c.close()


if __name__ == '__main__':
    main()
