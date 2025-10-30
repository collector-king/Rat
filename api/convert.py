import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from flask import Flask, request, send_file, abort, Response, stream_with_context
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB (Vercel-friendly)

# ----------------------------------------------------------------------
# Helper: create a tiny wrapper that injects the token
# ----------------------------------------------------------------------
def inject_token(py_path: Path, token: str, wrapper_path: Path):
    original = py_path.read_text(encoding="utf-8")
    wrapper = f"""\
import os
import runpy

os.environ["BOT_TOKEN"] = {repr(token)}

# Run the original script
runpy.run_path(r"{py_path}", run_name="__main__")
"""
    wrapper_path.write_text(wrapper, encoding="utf-8")


# ----------------------------------------------------------------------
@app.route("/convert", methods=["POST"])
def convert():
    token = request.form.get("token", "").strip()
    if not token:
        abort(400, "Bot token is required")

    file = request.files.get("pyfile")
    if not file or not file.filename.endswith(".py"):
        abort(400, "Valid .py file required")

    # ---- 1. Save upload in a unique temp folder ----------------------
    # Use /tmp for Vercel (ephemeral, but fine per-request)
    work_dir = Path("/tmp") / str(uuid.uuid4())
    work_dir.mkdir(parents=True, exist_ok=True)

    py_path = work_dir / secure_filename(file.filename)
    file.save(py_path)

    # ---- 2. Create wrapper -----------------------------------------
    wrapper_path = work_dir / "main_wrapper.py"
    try:
        inject_token(py_path, token, wrapper_path)
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, f"Wrapper error: {e}")

    # ---- 3. Run PyInstaller -----------------------------------------
    exe_name = py_path.stem + ".exe"
    dist_dir = work_dir / "dist"
    build_dir = work_dir / "build"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",                     # remove if you need a console
        f"--distpath={dist_dir}",
        f"--workpath={build_dir}",
        f"--specpath={work_dir}",
        str(wrapper_path)
    ]

    try:
        # Capture output for debugging (Vercel logs it)
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, f"PyInstaller failed: {e.stderr}")

    exe_path = dist_dir / exe_name
    if not exe_path.is_file():
        shutil.rmtree(work_dir, ignore_errors=True)
        abort(500, "EXE not produced")

    # ---- 4. Stream the EXE back --------------------------------------
    def generate():
        with open(exe_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk
        # Cleanup after streaming
        shutil.rmtree(work_dir, ignore_errors=True)

    response = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = f"attachment; filename={exe_name}"
    response.headers["X-Filename"] = exe_name
    return response


# Vercel serverless handler
def handler(request):
    return app(request.environ, lambda *args, **kwargs: None)

if __name__ == "__main__":
    app.run()
