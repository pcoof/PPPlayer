"""配置持久化存储 — 后端 JSON 文件存储

替代 pywebview localStorage，解决不同会话间 origin 变化导致配置丢失的问题。
"""

import json
import os
import threading

# 数据文件路径：项目根目录下的 data/config.json
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
_CONFIG_FILE = os.path.join(_DATA_DIR, "config.json")

_lock = threading.Lock()


def _ensure_dir():
    """确保 data 目录存在"""
    if not os.path.isdir(_DATA_DIR):
        os.makedirs(_DATA_DIR, exist_ok=True)


def load_all() -> dict:
    """读取全部配置，返回 dict。文件不存在时返回空 dict。"""
    with _lock:
        if not os.path.isfile(_CONFIG_FILE):
            return {}
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}


def save_all(data: dict) -> None:
    """保存全部配置到 JSON 文件（覆盖写）"""
    with _lock:
        _ensure_dir()
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def get_item(key: str, default=None):
    """读取单个配置项"""
    data = load_all()
    return data.get(key, default)


def set_item(key: str, value) -> None:
    """保存单个配置项（读取 → 修改 → 写回）"""
    with _lock:
        data = {}
        if os.path.isfile(_CONFIG_FILE):
            try:
                with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except (json.JSONDecodeError, OSError):
                data = {}
        data[key] = value
        _ensure_dir()
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
