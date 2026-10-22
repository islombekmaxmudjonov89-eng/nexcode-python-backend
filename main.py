
import os
import sys
import tempfile
import subprocess
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="NEXCODE AI Studio API")

API_KEY = os.environ.get("NEXCODE_API_KEY", "")


class CodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4000)


@app.get("/")
def home():
    return {"name": "NEXCODE AI Studio", "status": "online"}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/python/execute")
def execute_python(
    request: CodeRequest,
    x_api_key: str = Header(default="")
):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Basic limits for personal testing; this is NOT a secure sandbox.
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = Path(temp_dir) / "program.py"
        file_path.write_text(request.code, encoding="utf-8")

        try:
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(file_path)],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                timeout=3,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONIOENCODING": "utf-8",
                },
            )

            return {
                "output": result.stdout[-8000:],
                "error": result.stderr[-8000:],
                "return_code": result.returncode,
            }

        except subprocess.TimeoutExpired:
            return {
                "output": "",
                "error": "Execution timed out (3 seconds).",
                "return_code": -1,
            }
