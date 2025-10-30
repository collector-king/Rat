# api/index.py
import os
import shutil
import subprocess
import sys
import uuid
import logging
from pathlib import Path
from flask import Flask, request, abort, Response, stream_with_context

# Enable full logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB max

# ----------------------------------------------------------------------
# Serve the HTML page
# ----------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    html_path = Path(__file__).parent.parent / "templates" / "index.html"
    if not html_path.exists():
        log.error("index.html not found")
        return "HTML template missing", 404
    return html_path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# Inject token + source code into wrapper
# ----------------------------------------------------------------------
def create_wrapper(source_code: str, token: str, wrapper_path: Path):
    """
    Creates a wrapper that:
    1. Sets BOT_TOKEN
    2. Executes user code in a module
    """
    wrapper = f'''\
import os
import types
import sys

# Inject token
os.environ["BOT_TOKEN"] = {repr(token)}

# Execute user code
user_code = {repr(source_code)}
module = types.ModuleType("__main__")
sys.modules["__main__"] = module
exec(user_code, module.__dict__)
'''
    wrapper_path.write_text(wrapper, encoding="utf-8")
    log.info(f"Wrapper created at {wrapper_path}")


# ----------------------------------------------------------------------
# /api/convert – Main conversion endpoint
# ----------------------------------------------------------------------
@app.route("/api/convert", methods=["POST"])
def convert():
    try:
        # Get form data
        token = request.form.get("token", "").strip()
        source = request.form.get("source", "").strip()

        if not token:
            log.warning("Missing token")
            abort(400, "Bot token is required")
        if not source:
            log.warning("Empty source code")
            abort(400, "Source code is empty")

        log.info(f"Received request: token=***, source_length={len(source)}")

        # Create temp working directory
        work_dir = Path("/tmp") / f"py2exe_{uuid.uuid4().hex}"
        work_dir.mkdir(parents=True, exist_ok=True)
        log.debug(f"Work dir: {work_dir}")

        # Write wrapper
        wrapper_path = work_dir / "main_wrapper.py"
        try:
            create_wrapper(source, token, wrapper_path)
        except Exception as e:
            log.error(f"Failed to create wrapper: {e}")
            shutil.rmtree(work_dir, ignore_errors=True)
            abort(500, f"Wrapper creation failed: {e}")

        # PyInstaller paths
        exe_name = "bot.exe"
        dist_dir = work_dir / "dist"
        build_dir = work_dir / "build"
        spec_dir = work_dir

        # PyInstaller command
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--onefile",
            "--noconsole",           # Remove if you want console
            "--clean",
            f"--distpath={dist_dir}",
            f"--workpath={build_dir}",
            f"--specpath={spec_dir}",
            str(wrapper_path)
        ]

        log.info(f"Running PyInstaller: {' '.join(cmd)}")

        # Run PyInstaller
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=120  # 2 minutes max
            )
            log.info("PyInstaller success")
            log.debug(f"stdout: {result.stdout}")
            if result.stderr:
                log.warning(f"PyInstaller warnings: {result.stderr}")
        except subprocess.TimeoutExpired:
            log.error("PyInstaller timed out")
            shutil.rmtree(work_dir, ignore_errors=True)
            abort(500, "Build timed out (120s)")
        except subprocess.CalledProcessError as e:
            log.error(f"PyInstaller failed: {e.stderr}")
            shutil.rmtree(work_dir, ignore_errors=True)
            abort(500, f"Build failed: {e.stderr[:300]}")
        except Exception as e:
            log.error(f"Unexpected PyInstaller error: {e}")
            shutil.rmtree(work_dir, ignore_errors=True)
            abort(500, f"Build error: {e}")

        # Check output
        exe_path = dist_dir / exe_name
        if not exe_path.is_file():
            log.error("EXE not found after build")
            shutil.rmtree(work_dir, ignore_errors=True)
            abort(500, "EXE was not created")

        log.info(f"EXE created: {exe_path} ({exe_path.stat().st_size} bytes)")

        # Stream file + cleanup
        def generate():
            try:
                with open(exe_path, "rb") as f:
                    while chunk := f.read(8192):
                        yield chunk
            except Exception as e:
                log.error(f"Stream error: {e}")
            finally:
                try:
                    shutil.rmtree(work_dir, ignore_errors=True)
                    log.info("Cleanup complete")
                except:
                    pass

        response = Response(
            stream_with_context(generate()),
            mimetype="application/octet-stream"
        )
        response.headers["Content-Disposition"] = f"attachment; filename={exe_name}"
        response.headers["X-Filename"] = exe_name
        return response

    except Exception as e:
        log.exception("Unexpected error in /api/convert")
        return f"Server error: {str(e)}", 500


# ----------------------------------------------------------------------
# Local testing only
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
