"""Settings dialog: character, AI, voice, vision and appearance."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..config import DEFAULT_PERSONALITY, DEFAULT_SYSTEM, Config

MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]   # editable
VOICES = ["en-US-AnaNeural", "en-US-AriaNeural", "en-US-JennyNeural", "en-GB-MaisieNeural",
          "en-AU-NatashaNeural", "ja-JP-NanamiNeural", "zh-CN-XiaoyiNeural", "ko-KR-SunHiNeural"]


def _dspin(lo, hi, step, val, dec=2):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setValue(float(val))
    return s


def _ispin(lo, hi, val):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(val))
    return s


class SettingsDialog(QDialog):
    test_voice = Signal()
    clear_memory = Signal()

    def __init__(self, cfg: Config, memory=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.memory = memory
        self.setWindowTitle("DesktopWaifu Settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(560, 560)
        tabs = QTabWidget()

        # Character
        w = QWidget()
        f = QFormLayout(w)
        self.name = QLineEdit(cfg.name)
        self.user_name = QLineEdit(cfg.user_name, placeholderText="What she calls you")
        self.personality = QPlainTextEdit(cfg.personality)
        self.system = QPlainTextEdit(cfg.system_instructions)
        reset = QPushButton("Reset personality to default")
        reset.clicked.connect(lambda: (self.personality.setPlainText(DEFAULT_PERSONALITY),
                                       self.system.setPlainText(DEFAULT_SYSTEM)))
        f.addRow("Her name", self.name)
        f.addRow("Your name", self.user_name)
        f.addRow("Personality", self.personality)
        f.addRow("System instructions", self.system)
        f.addRow("", reset)
        tabs.addTab(w, "Character")

        # AI
        w = QWidget()
        f = QFormLayout(w)
        self.api_key = QLineEdit(cfg.api_key, echoMode=QLineEdit.EchoMode.Password,
                                 placeholderText="Paste your Gemini API key")
        link = QLabel('<a href="https://aistudio.google.com/apikey">Get a free key at Google AI Studio</a>'
                      '<br><small>The GEMINI_API_KEY environment variable overrides this field.</small>',
                      openExternalLinks=True)
        self.model = QComboBox(editable=True)
        self.model.addItems(MODELS)
        self.model.setCurrentText(cfg.model)
        self.temperature = _dspin(0, 2, 0.1, cfg.temperature)
        self.history = _ispin(2, 200, cfg.max_history_turns)
        clear = QPushButton("Forget everything (chat history, facts, moments)…")
        clear.clicked.connect(self._wipe)
        f.addRow("API key", self.api_key)
        f.addRow("", link)
        f.addRow("Model", self.model)
        f.addRow("Temperature", self.temperature)
        f.addRow("Memory (turns)", self.history)
        f.addRow("", clear)

        # local, downloadable model
        self.engine = QComboBox()
        self.engine.addItem("Gemini online, local model when Gemini is busy/offline", "auto")
        self.engine.addItem("Gemini online only", "gemini")
        self.engine.addItem("Local downloaded model only (offline, private)", "local")
        self.engine.setCurrentIndex(max(0, self.engine.findData(cfg.ai_engine)))
        from ..local_ai import SUGGESTED
        self.local_model = QComboBox(editable=True)
        for name, desc in SUGGESTED:
            self.local_model.addItem(name)
            self.local_model.setItemData(self.local_model.count() - 1, desc, Qt.ItemDataRole.ToolTipRole)
        self.local_model.setCurrentText(cfg.local_model)
        self.local_status = QLabel("", wordWrap=True, openExternalLinks=True)
        install = QPushButton("Install Ollama…")
        install.clicked.connect(self._install_ollama)
        download = QPushButton("Download model")
        download.clicked.connect(self._download_model)
        check = QPushButton("Check")
        check.clicked.connect(self._check_local)
        row_l = QHBoxLayout()
        for b in (install, download, check):
            row_l.addWidget(b)
        f.addRow("AI engine", self.engine)
        f.addRow("Local model", self.local_model)
        f.addRow("", row_l)
        f.addRow("", self.local_status)
        tabs.addTab(w, "AI")
        QTimer.singleShot(100, self._check_local)

        # Voice
        w = QWidget()
        f = QFormLayout(w)
        self.tts_enabled = QCheckBox("Speak replies out loud", checked=cfg.tts_enabled)
        self.tts_engine = QComboBox()
        self.tts_engine.addItem("Edge neural voices (online, free)", "edge")
        self.tts_engine.addItem("Windows SAPI (offline)", "sapi")
        self.tts_engine.setCurrentIndex(max(0, self.tts_engine.findData(cfg.tts_engine)))
        self.voice = QComboBox(editable=True)
        self.voice.addItems(VOICES)
        self.voice.setCurrentText(cfg.tts_voice)
        self.rate = QLineEdit(cfg.tts_rate)
        self.pitch = QLineEdit(cfg.tts_pitch)
        self.volume = _dspin(0, 1, 0.05, cfg.tts_volume)
        self.silence = _dspin(0.4, 5, 0.1, cfg.stt_silence_seconds, 1)
        self.mic_device = _ispin(-1, 99, cfg.mic_device)
        test = QPushButton("Test voice")
        test.clicked.connect(self._test)
        f.addRow(self.tts_enabled)
        f.addRow("Engine", self.tts_engine)
        f.addRow("Voice", self.voice)
        f.addRow("Rate (e.g. +10%)", self.rate)
        f.addRow("Pitch (e.g. +20Hz)", self.pitch)
        f.addRow("Volume", self.volume)
        f.addRow("Stop listening after pause (s)", self.silence)
        f.addRow("Mic device index (-1 = default)", self.mic_device)
        f.addRow("", test)
        tabs.addTab(w, "Voice")

        # Vision
        w = QWidget()
        f = QFormLayout(w)
        self.vision_auto = QCheckBox("Let her glance at my screen periodically", checked=cfg.vision_auto)
        self.vision_interval = _dspin(1, 240, 1, cfg.vision_interval_min, 0)
        self.vision_side = _ispin(480, 3840, cfg.vision_max_side)
        self.blocklist = QPlainTextEdit("\n".join(cfg.vision_blocklist))
        self.blocklist.setMaximumHeight(140)
        note = QLabel("Screenshots are sent to Google's Gemini API. She never looks while the active "
                      "window title contains one of the words below.", wordWrap=True)
        f.addRow(self.vision_auto)
        f.addRow("Every (minutes)", self.vision_interval)
        f.addRow("Max screenshot size (px)", self.vision_side)
        f.addRow(note)
        f.addRow("Never look at", self.blocklist)
        tabs.addTab(w, "Vision")

        # Appearance
        w = QWidget()
        f = QFormLayout(w)
        self.style = QComboBox()
        self.style.addItem("3D model (VRM or FBX, full body)", "vrm")
        self.style.addItem("Live picture (your image, with physics)", "image")
        self.style.addItem("Drawn scarf girl (dark hair, red scarf)", "scarf")
        self.style.addItem("Classic (pink hair, sailor dress)", "classic")
        self.style.setCurrentIndex(max(0, self.style.findData(cfg.builtin_style)))
        f.addRow("Look", self.style)
        self.vrm_path = QLineEdit(cfg.vrm_path, placeholderText="(first .vrm in assets/models)")
        browse_vrm = QPushButton("Browse…")
        browse_vrm.clicked.connect(self._pick_vrm)
        row_vrm = QHBoxLayout()
        row_vrm.addWidget(self.vrm_path, 1)
        row_vrm.addWidget(browse_vrm)
        f.addRow("3D model file", row_vrm)
        self.image_pack = QLineEdit(cfg.image_pack, placeholderText="(bundled scarf girl)")
        browse_img = QPushButton("Browse…")
        browse_img.clicked.connect(lambda: self._pick(self.image_pack, "Folder with character.png + character.json"))
        row_img = QHBoxLayout()
        row_img.addWidget(self.image_pack, 1)
        row_img.addWidget(browse_img)
        f.addRow("Live picture folder", row_img)
        self.touch_hints = QCheckBox("Show what each body part does when hovering", checked=cfg.touch_hints)
        f.addRow(self.touch_hints)
        self.ai_touch = QCheckBox("Gemini reacts to touches (otherwise instant preset lines)",
                                  checked=cfg.ai_touch_reactions)
        f.addRow(self.ai_touch)
        self.scale = _dspin(0.4, 3, 0.1, cfg.scale)
        self.walk_speed = _dspin(10, 400, 10, cfg.walk_speed, 0)
        self.activity = _dspin(0, 1, 0.1, cfg.activity)
        self.on_top = QCheckBox("Always on top", checked=cfg.always_on_top)
        self.hit_mask = QCheckBox("Force click-through mask (use if clicks on empty space get blocked)",
                                  checked=cfg.force_hit_mask)
        self.pack = QLineEdit(cfg.sprite_pack, placeholderText="(built-in character)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.pack, 1)
        row.addWidget(browse)
        self.bubble = _dspin(2, 60, 1, cfg.bubble_seconds, 0)
        f.addRow("Size", self.scale)
        f.addRow("Walk speed (px/s)", self.walk_speed)
        f.addRow("Activity", self.activity)
        f.addRow("Sprite pack folder", row)
        f.addRow("Bubble time (s)", self.bubble)
        f.addRow(self.on_top)
        f.addRow(self.hit_mask)
        tabs.addTab(w, "Appearance")

        # Senses & life
        w = QWidget()
        f = QFormLayout(w)
        self.webcam = QCheckBox("Let her see me through the webcam", checked=cfg.webcam_enabled)
        self.webcam_index = _ispin(0, 9, cfg.webcam_index)
        self.webcam_greet = QCheckBox("Greet me (sends one webcam photo to Gemini) when I come back",
                                      checked=cfg.webcam_greet)
        self.music = QCheckBox("Listen to music with me (beat sync runs locally)", checked=cfg.music_enabled)
        self.music_audio = QCheckBox("Let her really hear songs (sends an 8 s clip to Gemini)",
                                     checked=cfg.music_send_audio)
        self.wake_voice = QCheckBox("While she sleeps, listen for her name / \"wake up\" on the mic",
                                    checked=cfg.wake_by_voice)
        self.initiative = _dspin(0, 240, 5, cfg.initiative_minutes, 0)
        self.sleep_after = _dspin(0, 240, 1, cfg.sleep_after_min, 0)
        self.attention_min = _dspin(0, 120, 1, cfg.attention_minutes, 0)
        note = QLabel("Presence detection, beat detection and wake-word listening are processed on this PC. "
                      "Only explicit looks, greetings, song clips and speech you address to her are sent to Gemini.",
                      wordWrap=True)
        f.addRow(self.webcam)
        f.addRow("Webcam index", self.webcam_index)
        f.addRow(self.webcam_greet)
        f.addRow(self.music)
        f.addRow(self.music_audio)
        f.addRow(self.wake_voice)
        f.addRow("Start a chat after silence (min, 0 = never)", self.initiative)
        f.addRow("Nap after you're idle (min, 0 = never)", self.sleep_after)
        f.addRow("Seek your attention after (min, 0 = never)", self.attention_min)
        f.addRow(note)
        tabs.addTab(w, "Senses")

        # Memory
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("What she remembers about you. Select a line and delete it to make her forget it."))
        self.facts = QListWidget()
        v.addWidget(self.facts, 1)
        row = QHBoxLayout()
        delete = QPushButton("Forget selected")
        delete.clicked.connect(self._forget_selected)
        wipe = QPushButton("Forget everything…")
        wipe.clicked.connect(self._wipe)
        row.addWidget(delete)
        row.addStretch(1)
        row.addWidget(wipe)
        v.addLayout(row)
        self._reload_facts()
        tabs.addTab(w, "Memory")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)

    # ------------------------------------------------------------------ local model
    def _engine(self):
        from ..local_ai import OllamaEngine
        return OllamaEngine(self.cfg.ollama_url, self.local_model.currentText().strip())

    def _check_local(self) -> None:
        from ..workers import run_async
        eng = self._engine()

        def work():
            if not eng.installed() and not eng.running():
                return "not_installed", []
            eng.start_server()
            import time
            for _ in range(10):
                if eng.running():
                    break
                time.sleep(0.5)
            return ("running" if eng.running() else "stopped"), eng.models()

        def done(res):
            state, models = res
            if state == "not_installed":
                self.local_status.setText('Ollama is not installed. Click <b>Install Ollama…</b> or get it at '
                                          '<a href="https://ollama.com/download">ollama.com/download</a>.')
            elif state == "stopped":
                self.local_status.setText("Ollama is installed but not running. Start the Ollama app, then Check.")
            else:
                have = eng.model in models or f"{eng.model}:latest" in models
                self.local_status.setText(
                    f"Ollama running. Installed models: {', '.join(models) or 'none'}. "
                    + ("✅ Selected model is ready." if have else "⬇ Click <b>Download model</b> to get the selected one."))

        self.local_status.setText("Checking…")
        run_async(work, done, lambda m: self.local_status.setText(f"Check failed: {m}"))

    def _install_ollama(self) -> None:
        if QMessageBox.question(self, "Install Ollama?",
                                "Install Ollama (local AI runtime, ~1 GB) using winget from the official winget "
                                "repository? You can also download it yourself from ollama.com.") \
                != QMessageBox.StandardButton.Yes:
            return
        import subprocess
        from ..workers import run_async
        self.local_status.setText("Installing Ollama… (this can take a few minutes)")
        run_async(lambda: subprocess.run(["winget", "install", "--id", "Ollama.Ollama", "-e", "--source", "winget",
                                          "--accept-package-agreements", "--accept-source-agreements"],
                                         capture_output=True, text=True, creationflags=0x08000000).returncode,
                  lambda rc: (self.local_status.setText("Ollama installed." if rc == 0 else f"winget exit code {rc}"),
                              self._check_local()),
                  lambda m: self.local_status.setText(f"Install failed: {m}"))

    def _download_model(self) -> None:
        from ..workers import run_async
        eng = self._engine()
        name = eng.model
        if not eng.running():
            self._check_local()
            return
        self._progress = ""
        timer = QTimer(self)
        timer.timeout.connect(lambda: self.local_status.setText(self._progress))
        timer.start(300)

        def prog(status, frac):
            self._progress = f"Downloading {name}: {status} {frac * 100:.0f}%"

        def done(_):
            timer.stop()
            self.local_status.setText(f"✅ {name} downloaded. Choose an AI engine that uses it and Save.")

        def fail(m):
            timer.stop()
            self.local_status.setText(f"Download failed: {m}")

        run_async(lambda: eng.pull(name, prog), done, fail)

    def _reload_facts(self) -> None:
        self.facts.clear()
        if not self.memory:
            return
        for fid, cat, fact in self.memory.facts(500):
            it = QListWidgetItem(f"[{cat}] {fact}")
            it.setData(Qt.ItemDataRole.UserRole, fid)
            self.facts.addItem(it)

    def _forget_selected(self) -> None:
        for it in self.facts.selectedItems():
            self.memory.delete_fact(it.data(Qt.ItemDataRole.UserRole))
        self._reload_facts()

    def _wipe(self) -> None:
        if QMessageBox.question(self, "Forget everything?",
                                "Erase all chat history, facts and shared moments? This can't be undone.") \
                == QMessageBox.StandardButton.Yes:
            self.clear_memory.emit()
            self._reload_facts()

    def _pick_vrm(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Choose a 3D model", "", "3D models (*.vrm *.fbx)")
        if f:
            self.vrm_path.setText(f)

    def _pick(self, field: QLineEdit, title: str) -> None:
        d = QFileDialog.getExistingDirectory(self, title)
        if d:
            field.setText(d)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose a folder containing sheet.json")
        if d:
            self.pack.setText(d)

    def _test(self) -> None:
        self._write(self.cfg)
        self.test_voice.emit()

    def _write(self, c: Config) -> None:
        c.name = self.name.text().strip() or "Hana"
        c.user_name = self.user_name.text().strip()
        c.personality = self.personality.toPlainText().strip()
        c.system_instructions = self.system.toPlainText().strip()
        c.api_key = self.api_key.text().strip()
        c.model = self.model.currentText().strip() or "gemini-3.5-flash"
        c.temperature = self.temperature.value()
        c.ai_engine = self.engine.currentData()
        c.local_model = self.local_model.currentText().strip() or "gemma3:4b"
        c.max_history_turns = self.history.value()
        c.tts_enabled = self.tts_enabled.isChecked()
        c.tts_engine = self.tts_engine.currentData()
        c.tts_voice = self.voice.currentText().strip()
        c.tts_rate = self.rate.text().strip() or "+0%"
        c.tts_pitch = self.pitch.text().strip() or "+0Hz"
        c.tts_volume = self.volume.value()
        c.stt_silence_seconds = self.silence.value()
        c.mic_device = self.mic_device.value()
        c.vision_auto = self.vision_auto.isChecked()
        c.vision_interval_min = self.vision_interval.value()
        c.vision_max_side = self.vision_side.value()
        c.vision_blocklist = [l.strip() for l in self.blocklist.toPlainText().splitlines() if l.strip()]
        c.scale = self.scale.value()
        c.walk_speed = self.walk_speed.value()
        c.activity = self.activity.value()
        c.always_on_top = self.on_top.isChecked()
        c.force_hit_mask = self.hit_mask.isChecked()
        c.sprite_pack = self.pack.text().strip()
        c.builtin_style = self.style.currentData()
        c.image_pack = self.image_pack.text().strip()
        c.vrm_path = self.vrm_path.text().strip()
        c.webcam_enabled = self.webcam.isChecked()
        c.webcam_index = self.webcam_index.value()
        c.webcam_greet = self.webcam_greet.isChecked()
        c.music_enabled = self.music.isChecked()
        c.music_send_audio = self.music_audio.isChecked()
        c.wake_by_voice = self.wake_voice.isChecked()
        c.initiative_minutes = self.initiative.value()
        c.sleep_after_min = self.sleep_after.value()
        c.attention_minutes = self.attention_min.value()
        c.touch_hints = self.touch_hints.isChecked()
        c.ai_touch_reactions = self.ai_touch.isChecked()
        c.bubble_seconds = self.bubble.value()

    def accept(self) -> None:
        self._write(self.cfg)
        self.cfg.save()
        super().accept()
