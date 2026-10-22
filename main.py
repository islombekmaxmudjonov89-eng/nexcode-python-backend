
import os
import sys
import uuid
import queue
import threading
import tempfile
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="NEXCODE AI Studio API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

sessions = {}
sessions_lock = threading.Lock()
MAX_SESSIONS = 10
TIMEOUT_SECONDS = 15


class CodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4000)


class InputRequest(BaseModel):
    value: str = Field(max_length=2000)


def read_stream(stream, output_queue):
    try:
        while True:
            char = stream.read(1)
            if not char:
                break
            output_queue.put(char)
    finally:
        stream.close()


def stop_session(session_id):
    with sessions_lock:
        session = sessions.get(session_id)

    if session:
        process = session["process"]
        if process.poll() is None:
            process.kill()


def expire_session(session_id):
    threading.Event().wait(TIMEOUT_SECONDS)

    with sessions_lock:
        session = sessions.get(session_id)

    if session and session["process"].poll() is None:
        session["output"].put(
            "\nExecution timed out (15 seconds).\n"
        )
        session["process"].kill()


@app.get("/")
def home():
    return {
        "name": "NEXCODE AI Studio",
        "status": "online"
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/python/start")
def start_python(request: CodeRequest):
    with sessions_lock:
        active = sum(
            1 for session in sessions.values()
            if session["process"].poll() is None
        )

        if active >= MAX_SESSIONS:
            raise HTTPException(
                status_code=429,
                detail="Server busy. Try again later."
            )

    temp_dir = tempfile.TemporaryDirectory()
    file_path = Path(temp_dir.name) / "program.py"
    file_path.write_text(request.code, encoding="utf-8")

    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-I",
                "-S",
                str(file_path),
            ],
            cwd=temp_dir.name,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
            },
        )
    except Exception:
        temp_dir.cleanup()
        raise HTTPException(
            status_code=500,
            detail="Could not start Python."
        )

    session_id = uuid.uuid4().hex
    output = queue.Queue()

    with sessions_lock:
        sessions[session_id] = {
            "process": process,
            "output": output,
            "temp_dir": temp_dir,
        }

    threading.Thread(
        target=read_stream,
        args=(process.stdout, output),
        daemon=True,
    ).start()

    threading.Thread(
        target=expire_session,
        args=(session_id,),
        daemon=True,
    ).start()

    return {"session_id": session_id}


@app.post("/api/python/input/{session_id}")
def send_input(session_id: str, request: InputRequest):
    with sessions_lock:
        session = sessions.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    process = session["process"]

    if process.poll() is not None:
        raise HTTPException(status_code=409, detail="Program has finished")

    try:
        process.stdin.write(request.value + "\n")
        process.stdin.flush()
    except (BrokenPipeError, OSError):
        raise HTTPException(status_code=409, detail="Input is no longer accepted")

    return {"ok": True}


@app.get("/api/python/poll/{session_id}")
def poll_output(session_id: str):
    with sessions_lock:
        session = sessions.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    output = session["output"]
    chunks = []

    while len(chunks) < 8000:
        try:
            chunks.append(output.get_nowait())
        except queue.Empty:
            break

    process = session["process"]
    finished = process.poll() is not None

    result = {
        "output": "".join(chunks),
        "running": not finished,
        "return_code": process.poll(),
    }

    if finished:
        session["temp_dir"].cleanup()

    return result


@app.post("/api/python/stop/{session_id}")
def stop_python(session_id: str):
    with sessions_lock:
        session = sessions.get(session_id)

    if session and session["process"].poll() is None:
        session["process"].kill()

    return {"ok": True}
