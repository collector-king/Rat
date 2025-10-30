import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from flask import Flask, request, send_file, abort, Response, stream_with_context
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB

# ----------------------------------------------------------------------
# Serve the HTML frontend
# ----------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    html_path = Path(__file__).parent.parent / "templates" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "index.html not found", 404


# ----------------------------------------------------------------------
# Helper: inject token into wrapper
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
# Convert endpoint
# ----------------------------------------------------------------------
@app.route("/api/convert", methods=["POST"])
def convert():
    token = request.form.get("token", "").strip()
    if not token:
        abort(400, "Bot token is required")

    file = request.files.get("pyfile")
    if not file or not file.filename.endswith(".py"):
        abort(400, "Valid .py file required")

    # Use /tmp – only writable dir on Vercel
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

    # PyInstaller command
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
        print(result.stdout)  # Log for Vercel
    except subprocess.CalledProcessError as e:
        print(e.stderr)
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, f"PyInstaller failed: {e.stderr[:200]}")

    exe_path = dist_dir / exe_name
    if not exe_path.is_file():
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, "EXE not produced")

    # Stream back + cleanup
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
# Vercel expects this export
# ----------------------------------------------------------------------
def handler(event, context=None):
    from flask import Flask
    # Vercel uses AWS Lambda-style event
    # We use `wsgi_app` directly
    return app.wsgi_app(event, context.start_response if context else None)

# Optional: for local testing
if __name__ == "__main__":
    app.run(debug=True)
