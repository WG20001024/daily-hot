"""发布用轻量代理：把发布平台注入的 $PORT 转发到本机 5000 端口的 daily-hot 容器。"""
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = 'http://127.0.0.1:5000'
HOP_HEADERS = {'connection', 'keep-alive', 'transfer-encoding', 'upgrade',
               'proxy-authenticate', 'proxy-authorization', 'te', 'trailers'}


class Proxy(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _forward(self):
        url = UPSTREAM + self.path
        body = None
        if cl := self.headers.get('Content-Length'):
            body = self.rfile.read(int(cl))
        req = urllib.request.Request(url, data=body, method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in HOP_HEADERS and k.lower() != 'host':
                req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            data, status, headers = resp.read(), resp.status, resp.headers
        except urllib.error.HTTPError as e:
            data, status, headers = e.read(), e.code, e.headers
        except Exception as e:
            msg = ('upstream error: ' + str(e)).encode()
            self.send_response(502)
            self.send_header('Content-Length', str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        self.send_response(status)
        for k, v in headers.items():
            if k.lower() not in HOP_HEADERS and k.lower() != 'content-length':
                self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _forward

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '3000'))
    ThreadingHTTPServer(('0.0.0.0', port), Proxy).serve_forever()
