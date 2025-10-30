import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from flask import Flask, request, abort, Response, stream_with_context
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB max

# ----------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    html_path = Path(__file__).parent.parent / "templates" / "index.html"
    return html_path.read_text(encoding="utf-8")

# ----------------------------------------------------------------------
def inject_token(py_path: Path, token: str, wrapper_path: Path):
    wrapper = f"""\
import os
import runpy

os.environ["BOT_TOKEN"] = {repr(token)}

runpy.run_path(r"{py_path}", run_name="__main__")
"""
    wrapper_path.write_text(wrapper, encoding="utf-8")

# ----------------------------------------------------------------------
@app.route("/api/convert", methods=["POST"])
def convert():
    token = request.form.get("token", "").strip()
    if not token:
        abort(400, "Bot token required")

    file = request.files.get("pyfile")
    if not file or not file.filename.endswith(".py"):
        abort(400, "Valid .py file required")

    work_dir = Path("/tmp") / str(uuid.uuid4())
    work_dir.mkdir(parents=True, exist_ok=True)

    py_path = work_dir / secure_filename(file.filename)
    file.save(py_path)

    wrapper_path = work_dir / "main_wrapper.py"
    try:
        inject_token(py_path, token, wrapper_path)
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, f"Wrapper error: {e}")

    exe_name = py_path.stem + ".exe"
    dist_dir = work_dir / "dist"
    build_dir = work_dir / "build"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        f"--distpath={dist_dir}",
        f"--workpath={build_dir}",
        f"--specpath={work_dir}",
        str(wrapper_path)
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, f"Build failed: {e.stderr[:200]}")

    exe_path = dist_dir / exe_name
    if not exe_path.is_file():
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, "EXE not created")

    def generate():
        with open(exe_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk
        shutil.rmtree(work_dir, ignore_errors=True)

    response = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = f"attachment; filename={exe_name}"
    response.headers["X-Filename"] = exe_name
    return response

# ----------------------------------------------------------------------
# Vercel handler
# ----------------------------------------------------------------------
def handler(event, context=None):
    from wsgiref.handlers import CGIHandler
    import io

    class LambdaWSGI:
        def __init__(self, app):
            self.app = app

        def __call__(self, event, context):
            body = event.get('body', b'')
            if event.get('isBase64Encoded', False):
                import base64
                body = base64.b64decode(body)

            environ = {
                'REQUEST_METHOD': event['httpMethod'],
                'SCRIPT_NAME': '',
                'PATH_INFO': event['path'],
                'QUERY_STRING': event['queryStringParameters'] or '',
                'SERVER_NAME': event['headers'].get('host', 'lambda'),
                'SERVER_PORT': event['headers'].get('x-forwarded-port', '80'),
                'HTTP_HOST': event['headers'].get('host', 'lambda'),
                'CONTENT_TYPE': event['headers'].get('content-type', ''),
                'CONTENT_LENGTH': str(len(body)),
                'wsgi.input': io.BytesIO(body),
                'wsgi.version': (1, 0),
                'wsgi.url_scheme': event['headers'].get('x-forwarded-proto', 'http'),
                'wsgi.multithread': False,
                'wsgi.multiprocess': True,
                'wsgi.run_once': True,
            }

            for key, value in event['headers'].items():
                key = key.replace('-', '_').upper()
                if key not in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
                    environ[f'HTTP_{key}'] = value

            response_parts = []
            def start_response(status, headers):
                response_parts.append(status)
                response_parts.append(headers)
                return lambda x: x

            result = self.app(environ, start_response)
            body = b''.join(result)

            status = response_parts[0]
            headers = response_parts[1]

            return {
                'statusCode': int(status.split()[0]),
                'headers': dict(headers),
                'body': body,
                'isBase64Encoded': False
            }

    return LambdaWSGI(app)(event, context)

# For local testing
if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    make_server('127.0.0.1', 5000, app).serve_forever()
