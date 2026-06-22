"""monitor_web.py - Web UI 监控（v3，P2.8）。

启动一个简单的 HTTP server，展示 batch_*.py 任务进度与汇总。
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Agnes 多模态 - 任务监控</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; background: #fafafa; color: #222; }
h1 { color: #333; }
table { border-collapse: collapse; width: 100%; max-width: 1200px; }
th, td { padding: 8px 12px; border: 1px solid #ddd; text-align: left; }
th { background: #f0f0f0; }
tr.success { background: #e8f5e9; }
tr.failed { background: #ffebee; }
tr.partial { background: #fff8e1; }
tr.skipped { background: #f5f5f5; color: #888; }
.summary { margin: 16px 0; padding: 12px; background: #fff; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
pre { background: #f5f5f5; padding: 8px; border-radius: 4px; overflow: auto; }
.empty { color: #888; font-style: italic; }
</style>
</head>
<body>
<h1>Agnes 多模态 - 任务监控</h1>
<div id="content">加载中...</div>
<script>
async function refresh() {
  const r = await fetch('/api/logs');
  const data = await r.json();
  let html = '';
  if (data.logs.length === 0) {
    html = '<p class="empty">暂无日志文件</p>';
  } else {
    // 只展示最新的一个
    const latest = data.logs[0];
    html += `<div class="summary">`;
    html += `<strong>最新日志：</strong> ${latest.name} <br/>`;
    html += `<strong>修改时间：</strong> ${latest.mtime} <br/>`;
    html += `<strong>状态汇总：</strong> <pre>${JSON.stringify(latest.summary, null, 2)}</pre>`;
    html += `</div>`;
    html += `<table>`;
    html += `<thead><tr><th>key</th><th>status</th><th>prompt</th><th>error_type</th><th>saved_path / video_url</th></tr></thead><tbody>`;
    for (const [k, v] of Object.entries(latest.items)) {
      const path = v.saved_path || v.saved_paths || v.video_url || '-';
      html += `<tr class="${v.status}"><td>${k}</td><td>${v.status}</td><td>${(v.prompt_used || v.prompt || '').slice(0, 60)}</td><td>${v.error_type || '-'}</td><td>${Array.isArray(path) ? path.join(', ') : path}</td></tr>`;
    }
    html += `</tbody></table>`;
  }
  document.getElementById('content').innerHTML = html;
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class MonitorHandler(BaseHTTPRequestHandler):
    logs_dir: Path = None

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode("utf-8"))
        elif self.path == "/api/logs":
            self._serve_logs()
        else:
            self.send_error(404)

    def _serve_logs(self) -> None:
        logs_dir = self.logs_dir
        if not logs_dir or not logs_dir.exists():
            payload = {"logs": []}
        else:
            files = sorted(logs_dir.glob("log_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            entries = []
            for f in files[:5]:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                # 汇总
                summary = {}
                items = {}
                for k, v in data.items():
                    if not isinstance(v, dict):
                        continue
                    s = v.get("status", "unknown")
                    summary[s] = summary.get(s, 0) + 1
                    items[k] = v
                entries.append({
                    "name": f.name,
                    "mtime": f.stat().st_mtime,
                    "summary": summary,
                    "items": items,
                })
            payload = {"logs": entries}

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # 静默
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Web UI 监控")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--logs-dir", default="assets/outputs", help="日志目录")
    args = parser.parse_args()

    MonitorHandler.logs_dir = Path(args.logs_dir).resolve()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), MonitorHandler)
    print(f"Monitor running at http://localhost:{args.port}/")
    print(f"Logs dir: {MonitorHandler.logs_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
