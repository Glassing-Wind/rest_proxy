import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('repository', type=Path)
ROOT = parser.parse_args().repository / 'src/public'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT), **kwargs)
    def log_message(self, *args): pass
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()
    def reply(self, value):
        payload=json.dumps(value).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(payload)
    def do_GET(self):
        if self.path.startswith('/api/applications/public/'):
            self.reply({'invite':{'email':'qa@example.test','firstName':'QA','lastName':'Applicant','phone':'5550100','property':{'name':'Fixture Property'},'unit':{'name':'Unit 1'}},'application':None}); return
        if self.path.startswith('/apply?'): self.path='/apply.html'
        super().do_GET()
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Path('/tmp/rental-qa-submit.json').write_text(json.dumps(body))
        self.reply({**body,'status':'SUBMITTED','consentToScreeningAt':None})
ThreadingHTTPServer(('127.0.0.1',8769), Handler).serve_forever()
