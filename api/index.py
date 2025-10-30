import os
from flask import Flask
from pathlib import Path

app = Flask(__name__)

@app.route("/")
def index():
    html_path = Path(__file__).parent.parent / "templates" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "index.html not found", 404

# Vercel serverless handler
def handler(request):
    return app(request.environ, lambda *args, **kwargs: None)

if __name__ == "__main__":
    app.run()
