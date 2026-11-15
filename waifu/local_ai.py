"""Local, downloadable AI brain via Ollama (https://ollama.com) — works offline and when Gemini is busy.

Ollama runs models on your PC and serves them on http://localhost:11434. Vision models
(gemma3, llama3.2-vision, qwen2.5vl) can also see screenshots / webcam photos.
Only the standard library is used (urllib), so no extra Python packages are needed.
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Callable

log = logging.getLogger(__name__)

SUGGESTED = [
    ("gemma3:4b", "Google Gemma 3 4B — vision, ~3.3 GB, good on most PCs"),
    ("gemma3:12b", "Gemma 3 12B — vision, ~8 GB, needs a strong GPU"),
    ("qwen2.5vl:7b", "Qwen 2.5 VL 7B — vision, ~6 GB"),
    ("llama3.2:3b", "Llama 3.2 3B — text only, ~2 GB, fast"),
]


class OllamaEngine:
    def __init__(self, url: str = "http://localhost:11434", model: str = "gemma3:4b"):
        self.url = url.rstrip("/")
        self.model = model

    # ------------------------------------------------------------------ status
    @staticmethod
    def installed() -> bool:
        if shutil.which("ollama"):
            return True
        import os
        return os.path.exists(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"))

    def _get(self, path: str, timeout: float = 2.0):
        with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def running(self) -> bool:
        try:
            self._get("/api/tags", 1.5)
            return True
        except Exception:  # noqa: BLE001
            return False

    def models(self) -> list[str]:
        try:
            return [m["name"] for m in self._get("/api/tags").get("models", [])]
        except Exception:  # noqa: BLE001
            return []

    def available(self) -> bool:
        names = self.models()
        if self.model in names or f"{self.model}:latest" in names:
            return True
        return ":" not in self.model and any(n.split(":")[0] == self.model for n in names)

    def start_server(self) -> None:
        """Start `ollama serve` in the background if Ollama is installed but not running."""
        if self.running() or not self.installed():
            return
        exe = shutil.which("ollama") or __import__("os").path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        subprocess.Popen([exe, "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------ chat
    def chat(self, system: str, history: list[tuple[str, str]], text: str, images: list[bytes] | None = None,
             temperature: float = 0.9, json_out: bool = False, timeout: float = 180.0) -> str:
        msgs = [{"role": "system", "content": system}] if system else []
        for role, t in history:
            msgs.append({"role": "assistant" if role == "model" else "user", "content": t})
        user = {"role": "user", "content": text}
        if images:
            user["images"] = [base64.b64encode(b).decode("ascii") for b in images]
        msgs.append(user)
        body = {"model": self.model, "messages": msgs, "stream": False,
                "options": {"temperature": temperature}, "keep_alive": "30m"}
        if json_out:
            body["format"] = "json"
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if images and "image" in detail.lower():     # text-only model: retry without the picture
                return self.chat(system, history, text + " (You can't see images with this model.)", None,
                                 temperature, json_out, timeout)
            raise RuntimeError(f"Local model error: {detail}") from e
        return (data.get("message") or {}).get("content", "").strip()

    # ------------------------------------------------------------------ download
    def pull(self, model: str, progress: Callable[[str, float], None] | None = None) -> None:
        """Download a model (streams progress). Blocking: run it in a worker thread."""
        req = urllib.request.Request(self.url + "/api/pull", data=json.dumps({"name": model}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3600) as r:
            for raw in r:
                try:
                    ev = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                if "error" in ev:
                    raise RuntimeError(ev["error"])
                total, done = ev.get("total") or 0, ev.get("completed") or 0
                if progress:
                    progress(ev.get("status", ""), done / total if total else 0.0)
