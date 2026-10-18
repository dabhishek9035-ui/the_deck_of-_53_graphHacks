"""Typed config loader. Everything configurable lives in config.yaml."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7777


class FalkorCfg(BaseModel):
    host: str = "localhost"
    port: int = 6379


class OllamaCfg(BaseModel):
    url: str = "http://localhost:11434"
    model: str = "qwen2.5-coder:7b"


class CooldownCfg(BaseModel):
    bubble_seconds: float = 15
    same_mistake_minutes: float = 5


class MockCfg(BaseModel):
    analysis: bool = True
    tutor: bool = True
    graph: bool = True


class VoiceCfg(BaseModel):
    enabled: bool = False
    hotkey: str = "ctrl+alt+space"
    speak_replies: bool = True
    whisper_model: str = "models/ggml-base.en.bin"
    whisper_cmd: list[str] = ["whisper-cli", "-m", "{model}", "-f", "{wav}", "-nt", "-np"]
    piper_model: str = "models/en_US-lessac-medium.onnx"
    piper_cmd: list[str] = ["piper", "--model", "{model}", "--output_file", "{out}"]


class Config(BaseModel):
    server: ServerCfg = Field(default_factory=ServerCfg)
    falkordb: FalkorCfg = Field(default_factory=FalkorCfg)
    ollama: OllamaCfg = Field(default_factory=OllamaCfg)
    debounce_seconds: float = 1.5
    llm_timeout_seconds: float = 30
    min_confidence: float = 0.25
    sleep_after_seconds: float = 300
    cooldowns: CooldownCfg = Field(default_factory=CooldownCfg)
    allowlisted_paths: list[str] = ["~/tutor-lessons"]
    default_student: str = "local-user"
    inspector: bool = False
    mock: MockCfg = Field(default_factory=MockCfg)
    voice: VoiceCfg = Field(default_factory=VoiceCfg)

    def allowlist(self) -> list[Path]:
        paths = [Path(p).expanduser().resolve() for p in self.allowlisted_paths]
        # Always allow repo lessons folder if present
        repo_root = Path(__file__).resolve().parents[1]
        repo_lessons = repo_root / "lessons"
        if repo_lessons.is_dir() and repo_lessons not in paths:
            paths.append(repo_lessons)
        return paths

    def is_allowed(self, file: str) -> bool:
        """True only for files inside lesson/{topic}/starter (and not obvious secret files)."""
        if not file:
            return False
        p = Path(file).expanduser().resolve()
        name = p.name.lower()
        if name.startswith(".env") or name.endswith((".pem", ".key")):
            return False
        parts = [part.lower() for part in p.parts]
        if "starter" not in parts:
            return False
        return any(p == r or r in p.parents for r in self.allowlist())


def load_config(path: str | None = None) -> Config:
    candidate_paths = [
        path,
        os.environ.get("TUTOR_CONFIG"),
        Path(__file__).resolve().parents[1] / "config.yaml",
        Path(__file__).resolve().with_name("config.yaml"),
    ]
    target_path = None
    for cp in candidate_paths:
        if cp and Path(cp).is_file():
            target_path = Path(cp)
            break
    data = yaml.safe_load(target_path.read_text(encoding="utf-8")) if target_path else {}
    return Config.model_validate(data or {})
