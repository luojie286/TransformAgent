"""Load model settings and keep credentials out of ordinary settings."""

import os
import winreg
from urllib.parse import urlsplit

import keyring
from PySide6.QtCore import QSettings

from transformagent.translation import BAILIAN_ENDPOINT, DEFAULT_MODEL, TranslationConfig


def qwen_environment_key() -> str:
    key = os.getenv("QWEN_API_KEY", "").strip()
    if key:
        return key
    # An already open launcher may have inherited its environment before the
    # user set this variable. Read the current user-scoped value in that case.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment:
            return str(winreg.QueryValueEx(environment, "QWEN_API_KEY")[0]).strip()
    except OSError:
        return ""


def endpoint_url(value: str) -> str:
    value = value.strip().rstrip("/")
    url = urlsplit(value)
    loopback = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not url.hostname or url.username or url.password or url.query or url.fragment
        or (url.scheme != "https" and not (loopback and url.scheme == "http"))
    ):
        raise ValueError("请输入 HTTPS 接口地址，或本机测试地址。")
    return value if url.path.endswith("/chat/completions") else value + "/chat/completions"


class Settings:
    def __init__(self, store=None):
        self.store = store if store is not None else QSettings("TransformAgent", "TransformAgent")

    def endpoint(self) -> str:
        return self.store.value("endpoint", BAILIAN_ENDPOINT, type=str)

    def model(self) -> str:
        return self.store.value("model", DEFAULT_MODEL, type=str)

    def config(self) -> TranslationConfig:
        override = os.getenv("TRANSFORMAGENT_API_URL")
        endpoint = endpoint_url(override or self.endpoint())
        model = os.getenv("TRANSFORMAGENT_MODEL") or self.model()
        key = os.getenv("TRANSFORMAGENT_API_KEY", "") if override else ""
        # Only send the Qwen environment key to the default, first-party endpoint.
        if not key and endpoint == BAILIAN_ENDPOINT:
            key = qwen_environment_key()
        if not key and not override:
            key = keyring.get_password("TransformAgent", endpoint) or ""
        return TranslationConfig(endpoint, model, key.strip())

    def save(self, address: str, model: str, key: str):
        endpoint = endpoint_url(address)
        model = model.strip()
        if not model:
            raise ValueError("请输入模型名称。")
        if key.strip():
            keyring.set_password("TransformAgent", endpoint, key.strip())
        self.store.setValue("endpoint", endpoint)
        self.store.setValue("model", model)
        self.store.sync()
