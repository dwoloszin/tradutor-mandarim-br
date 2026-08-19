"""Funções compartilhadas pelo scraper: requisições HTTP, parsing e caminhos de dados."""
import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "docs" / "data"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8,zh;q=0.7",
}

REQUEST_TIMEOUT = 8
REQUEST_DELAY = 0.2  # segundos entre requisições, para não sobrecarregar os sites alvo


def get_soup(url: str, **kwargs) -> BeautifulSoup | None:
    """Baixa uma URL e retorna um BeautifulSoup, ou None se falhar."""
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return BeautifulSoup(resp.text, "lxml")
    except requests.RequestException:
        return None


def absolute_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base, href)


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


def save_json(filename: str, data) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_json(filename: str, default=None):
    path = DATA_DIR / filename
    if not path.exists():
        return default if default is not None else []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
