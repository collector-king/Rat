# api/index.py
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from flask import Flask, request, abort, Response, stream_with_context

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

@app.route("/", methods=["GET"])
def index():
    html_path = Path(__file__).parent.parent / "templates" / "index.html"
    return html_path.read_text(encoding="utf-8")

def inject_token(source_code: str, token: str, wrapper_path: Path):
    wrapper = f"""\
import os
import runpy

os.environ["BOT_TOKEN"] = {repr(token)}

# === INJECTED SOURCE CODE ===
import types
module = types.ModuleType("__main__")
exec({repr(source_code)}, module.__dict__)
# ============================
"""
    wrapper_path.write_text(wrapper, encoding="utf-8")

@app.route("/api/convert", methods=["POST"])
def convert():
    token = request.form.get("token", "").strip()
    source = request.form.get("source", "").strip()
    
    if not token:
        abort(400, "Bot token required")
    if not source:
        abort(400, "Source code is empty")

    work_dir = Path("/tmp") / str(uuid.uuid4())
    work_dir.mkdir(parents=True, exist_ok=True)

    wrapper_path = work_dir / "main_wrapper.py"
    try:
        inject_token(source, token, wrapper_path)
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, f"Wrapper error: {e}")

    exe_name = "bot.exe"
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
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, f"Build failed: {e.stderr.decode()[:200]}")

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

# Local testing
if __name__ == "__main__":
    app.run(debug=True, port=5000)
