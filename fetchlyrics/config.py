"""Configuration loading with recursive merging of user overrides."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

__version__ = "0.1.0"
PROJECT_URL = "https://github.com/<username>/fetch-lyrics"

DEFAULT_CONFIG: dict[str, Any] = {
    "aliases": [
        ["tomorrow x together", "txt", "투모로우바이투게더"],
        ["bts", "bangtan", "방탄소년단"],
        ["iu", "이지은"],
        ["snsd", "girls' generation", "소녀시대"],
        ["g-idle", "(g)i-dle", "(여자)아이들", "gidle"],
        ["seventeen", "svt", "세븐틴"],
        ["stray kids", "skz", "스트레이 키즈"],
        ["le sserafim", "르세라핌"],
        ["newjeans", "뉴진스"],
        ["blackpink", "블랙핑크"],
        ["twice", "트와이스"],
    ],
    "cleaning": {
        "feature_regex": r"[\(\[][\s]*(?:feat\.?|featuring|ft\.?)\s+[^\)\]]+[\)\]]",
        "trailing_feature_regex": r"\s+(?:feat\.?|featuring|ft\.?)\s+.*$",
        "korean_bracket_regex": r"[\(\[][^\)\]]*[가-힣ㄱ-ㅎㅏ-ㅣ][^\)\]]*[\)\]]",
        "japanese_bracket_regex": r"[\(\[][^\)\]]*[ぁ-ゖァ-ヺ一-龥][^\)\]]*[\)\]]",
        "chinese_bracket_regex": r"[\(\[][^\)\]]*[一-龥][^\)\]]*[\)\]]",
        "ignore_words_artist_match": ["the", "and", "feat", "ft", "with", "&"],
    },
    "api": {
        "lrclib_url": "https://lrclib.net/api",
        "netease_search_url": "https://music.163.com/api/search/get/web",
        "netease_lyric_url": "https://music.163.com/api/song/lyric",
        "timeout_seconds": 6,
        "netease_search_limit": 6,
        "user_agent": f"fetch-lyrics/{__version__} ({PROJECT_URL})",
        "max_workers": 8,
        "retry_total": 2,
        "retry_backoff": 0.3,
        "duration_tolerance_seconds": 2,
        "enable_lrclib": True,
        "enable_netease": True,
        "enable_syncedlyrics": True,
    },
    "settings": {
        "supported_extensions": [".flac", ".mp3", ".ogg", ".opus", ".m4a"],
        "default_editor": "nano",
        "preview_lines": 16,
        "non_latin_ratio_threshold": 0.25,
        "max_search_depth": 3,
        "max_file_count": 250,
        "search_cache_entries": 64,
        "prefer_latin": True,
        "sort_by_tags": False,
        "write_sidecar_lrc": False,
        "id3_v2_version": 4,
        "uslt_language": "eng",
        "color": "auto",
    },
}


class ConfigError(Exception):
    """Raised when a configuration file exists but cannot be used."""


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*.

    Unlike a flat ``dict.update()`` this keeps default sub-keys alive when the
    user only overrides part of a section.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def config_search_paths() -> list[Path]:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg) if xdg else Path.home() / ".config"
    return [
        Path(__file__).resolve().parent.parent / "config.json",
        config_home / "fetch-lyrics" / "config.json",
    ]


def load_config(explicit_path: Path | None = None) -> tuple[dict, list[str]]:
    """Return ``(config, warnings)``.

    Defaults are always present; user values are merged on top recursively.
    """
    warnings: list[str] = []
    paths = [explicit_path] if explicit_path else config_search_paths()

    for path in paths:
        if path is None or not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Configuration file '{path}' is invalid, using defaults: {exc}")
            continue

        if not isinstance(loaded, dict):
            warnings.append(f"Configuration file '{path}' is not a JSON object, using defaults.")
            continue

        merged = _deep_merge(DEFAULT_CONFIG, loaded)
        merged["_source"] = str(path)
        return merged, warnings

    fallback = copy.deepcopy(DEFAULT_CONFIG)
    fallback["_source"] = "built-in defaults"
    return fallback, warnings


# Populated by the CLI entry point; imported lazily by the other modules.
CONFIG: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
CONFIG["_source"] = "built-in defaults"


def set_config(config: dict) -> None:
    global CONFIG
    CONFIG = config


def get(section: str, key: str, default: Any = None) -> Any:
    """Safe accessor that never raises KeyError on a missing section."""
    section_data = CONFIG.get(section)
    if not isinstance(section_data, dict):
        section_data = DEFAULT_CONFIG.get(section, {})
    if key in section_data:
        return section_data[key]
    return DEFAULT_CONFIG.get(section, {}).get(key, default)
