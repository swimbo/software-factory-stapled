#!/usr/bin/env python3
"""Probe controller-only SIGKILL with a live pinned Agent Server and fake model.

This is not a container-crash resume or a revision verification test: the
revision verifier is a deliberate stub. The fixture uses no real credentials.
"""

import sys, os, json, time, uuid, subprocess, tempfile
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER_DIR / "probes"))
from crash_recovery import cmd, IMAGE

name = "factory-controller-probe-" + uuid.uuid4().hex[:12]
MOCK = """import http.server,time,json,os\nclass H(http.server.BaseHTTPRequestHandler):\n def log_message(self,*a): pass\n def do_POST(self):\n  x=json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))))\n  with open("/tmp/mock-count","a") as f: f.write(json.dumps({"model":x.get("model"),"tools":[t["function"]["name"] for t in x.get("tools",[])]})+"\\n")\n  t=[i["function"] for i in x.get("tools",[]) if "finish" in i["function"]["name"].lower()]\n  open("/tmp/mock-arrived","w").write(json.dumps(t))\n  while not os.path.exists("/tmp/release"): time.sleep(.1)\n  n=t[0]["name"] if t else "finish"\n  y={"id":"probe","object":"chat.completion","created":0,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":None,"tool_calls":[{"id":"call_1","type":"function","function":{"name":n,"arguments":"{\\"message\\":\\"hello\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n  b=json.dumps(y).encode(); self.send_response(200);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(b)));self.end_headers();self.wfile.write(b)\nhttp.server.HTTPServer(("127.0.0.1",18080),H).serve_forever()\n"""
CHILD = """import json,sys,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[4])\nfrom guarded_store import GuardedAttemptStore\nfrom worker import execute_attempt\nfrom openhands_adapter import OpenHandsAdapter\nfrom docker_transport import DockerTransport\na=OpenHandsAdapter("http://127.0.0.1:8000","dummy",lambda *_:"a"*40,transport=DockerTransport(sys.argv[1],"dummy"))\nr=json.load(sys.stdin); st=GuardedAttemptStore(Path(sys.argv[2]),Path(sys.argv[3]))\ndef send(cid,p):\n a.create(cid,p)\n time.sleep(300)\nprint(execute_attempt(st,"test-attempt",r,send,a.read),flush=True)\n"""
created = False
child = None
try:
    cid = cmd(
        "run",
        "-d",
        "--name",
        name,
        "--label",
        "factory.probe=controller-recovery",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "-e",
        "OH_ENABLE_VSCODE=false",
        IMAGE,
        timeout=60,
    ).stdout.strip()
    created = True

    def inside(*a, input=None, check=True):
        return cmd("exec", "-i", name, *a, input=input, check=check)

    for i in range(60):
        r = inside(
            "python",
            "-c",
            'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)',
            check=False,
        )
        if r.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("not ready")
    inside(
        "python",
        "-c",
        'import sys;open("/tmp/mock.py","w").write(sys.stdin.read())',
        input=MOCK,
    )
    cmd("exec", "-d", name, "python", "/tmp/mock.py")
    req = {
        "agent_settings": {
            "llm": {
                "usage_id": "probe",
                "model": "openai/gpt-4o",
                "base_url": "http://127.0.0.1:18080/v1",
                "api_key": "dummy",
                "max_output_tokens": 128,
            }
        },
        "workspace": {"working_dir": "/tmp"},
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": "Say hello."}],
            "run": True,
        },
        "max_iterations": 3,
    }
    with tempfile.TemporaryDirectory() as d:
        os.mkdir(d + "/anchor")
        sys.path.insert(0, str(WORKER_DIR))
        from guarded_store import GuardedAttemptStore

        GuardedAttemptStore.initialize(
            __import__("pathlib").Path(d + "/attempt.db"),
            __import__("pathlib").Path(d + "/anchor/watermark"),
        )
        child = subprocess.Popen(
            [
                "python3",
                "-c",
                CHILD,
                cid,
                d + "/attempt.db",
                d + "/anchor/watermark",
                str(WORKER_DIR),
            ],
            stdin=subprocess.PIPE,
            text=True,
        )
        child.stdin.write(json.dumps(req))
        child.stdin.close()
        for i in range(60):
            if inside("test", "-f", "/tmp/mock-arrived", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("request not received")
        print("model request in flight; killing controller", flush=True)
        child.kill()
        child.wait()
        inside("touch", "/tmp/release")
        sys.path.insert(0, str(WORKER_DIR))
        from guarded_store import GuardedAttemptStore
        from worker import execute_openhands_attempt
        from openhands_adapter import OpenHandsAdapter
        from docker_transport import DockerTransport

        a = OpenHandsAdapter(
            "http://127.0.0.1:8000",
            "dummy",
            lambda *_: "a" * 40,
            transport=DockerTransport(cid, "dummy"),
        )
        st = GuardedAttemptStore(
            __import__("pathlib").Path(d + "/attempt.db"),
            __import__("pathlib").Path(d + "/anchor/watermark"),
        )
        for i in range(30):
            x = execute_openhands_attempt(
                st, "test-attempt", req, a, reconcile_only=True
            )
            print("reconcile", x, flush=True)
            if x["status"] == "completed":
                break
            time.sleep(1)
        else:
            raise RuntimeError("conversation did not complete")
        count = int(inside("sh", "-c", "wc -l < /tmp/mock-count").stdout.strip())
        entries = [
            json.loads(line)
            for line in inside("cat", "/tmp/mock-count").stdout.splitlines()
        ]
        if sum(bool(entry["tools"]) for entry in entries) != 1:
            raise RuntimeError("agent model request was repeated")
        print("model requests:", count, "(one tool-bearing agent request)", flush=True)
finally:
    if child and child.poll() is None:
        child.kill()
        child.wait()
    if created:
        cmd("rm", "-f", name, check=False, timeout=60)
