#!/usr/bin/env python3
"""local_runner — drive a qcbench batch through a local llama.cpp model.

Starts a private llama-server on loopback, streams the batch through
/v1/chat/completions one item at a time, writes qcbench answer JSONL, and
stops the server. Stdlib only.

RAM rule (see needs-sudo-shared-models.md): default model is qwen3.5-9B;
run the larger models only in agreed windows with the OpenClaw gateway idle.
"""
import argparse, json, os, signal, socket, subprocess, sys, time, urllib.request

RUNTIME = os.path.expanduser("~/.local/opt/llama.cpp/llama-b10604/llama-server")
MODELS = "/srv/models/llama.cpp/chat"
DEFAULT_MODEL = f"{MODELS}/qwen3.5-9b-q4_k_m.gguf"

def wait_ready(port, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if r.status == 200: return
        except Exception: time.sleep(2)
    raise SystemExit("llama-server did not become ready")

def ask(port, prompt, item, temp):
    body = json.dumps({
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": json.dumps(item["input"])}],
        "temperature": temp, "max_tokens": 200,
        "response_format": {"type": "json_object"},
        # Qwen3.5 defaults to thinking mode; on CPU that burns the whole
        # token budget in reasoning_content and returns empty content.
        "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--port", type=int, default=8091)  # never 18789 (gateway)
    ap.add_argument("--ctx", type=int, default=16384); ap.add_argument("--threads", type=int, default=12)
    ap.add_argument("--temp", type=float, default=0.0)
    ap.add_argument("--max-items", type=int, default=None)
    a = ap.parse_args()
    if not os.path.exists(a.model): sys.exit(f"model not found: {a.model} (handoff not run yet?)")
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", a.port)) == 0: sys.exit(f"port {a.port} already in use")
    lines = [json.loads(l) for l in open(a.batch) if l.strip()]
    header, items = lines[0], lines[1:]
    if a.max_items: items = items[:a.max_items]
    srv = subprocess.Popen(
        [RUNTIME, "-m", a.model, "--host", "127.0.0.1", "--port", str(a.port),
         "-c", str(a.ctx), "-t", str(a.threads), "--no-webui",
         "--reasoning-budget", "0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready(a.port)
        model_tag = os.path.basename(a.model)
        t0 = time.time(); done = 0
        with open(a.out, "w") as f:
            for it in items:
                try:
                    raw = ask(a.port, header["prompt"], it, a.temp)
                    ans = json.loads(raw)
                    rec = {"item_id": it["item_id"], "class": ans.get("class"),
                           "confidence": ans.get("confidence"), "reason": ans.get("reason"),
                           "model": model_tag}
                except Exception as e:
                    rec = {"item_id": it["item_id"], "class": None, "confidence": None,
                           "reason": f"runner_error:{type(e).__name__}", "model": model_tag}
                f.write(json.dumps(rec) + "\n"); f.flush()
                done += 1
                if done % 25 == 0:
                    rate = done / (time.time() - t0)
                    print(f"{done}/{len(items)}  {rate:.1f} items/s", file=sys.stderr)
        print(f"done: {done} items in {time.time()-t0:.0f}s -> {a.out}", file=sys.stderr)
    finally:
        srv.send_signal(signal.SIGTERM)
        try: srv.wait(timeout=15)
        except subprocess.TimeoutExpired: srv.kill()

if __name__ == "__main__":
    main()
