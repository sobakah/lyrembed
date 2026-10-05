"""Configuration loading with recursive merging of user overrides."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

__version__ = "0.3.0"
PROJECT_URL = "https://github.com/sobakah/lrckit"

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
        "netease_search_url": "https://music.163.com/api/search/get",
        "netease_lyric_url": "https://music.163.com/api/song/lyric",
        "timeout_seconds": 6,
        "netease_search_limit": 6,
        "user_agent": f"lrckit/{__version__} ({PROJECT_URL})",
        "max_workers": 8,
        "retry_total": 2,
        "retry_backoff": 0.3,
        "duration_tolerance_seconds": 2,
        "syncedlyrics_timeout_seconds": 15,
        "batch_delay_seconds": 1.0,
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
        "romanization_layout": "replace",
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
    """The user's config comes first; a ``config.json`` in a source checkout
    is still honoured after it, for setups that predate the XDG location."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg) if xdg else Path.home() / ".config"
    paths = [config_home / "lrckit" / "config.json"]
    checkout = Path(__file__).resolve().parent.parent
    if (checkout / "pyproject.toml").is_file():
        paths.append(checkout / "config.json")
    return paths


def _matches_type(value: Any, default: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, list):
        if not isinstance(value, list):
            return False
        return not default or all(_matches_type(item, default[0]) for item in value)
    return isinstance(value, type(default))


def _validate(merged: dict, user: dict, source: Path) -> list[str]:
    """Replace values of the wrong type with the default and report them.

    Without this a string where a list belongs is iterated character by
    character, and a non-numeric limit crashes deep inside a worker thread.
    """
    warnings: list[str] = []
    for section, values in user.items():
        if section not in DEFAULT_CONFIG:
            warnings.append(f"{source}: unknown section '{section}' is ignored.")
            continue
        default_section = DEFAULT_CONFIG[section]
        if not _matches_type(values, default_section):
            warnings.append(f"{source}: '{section}' has the wrong type, using the defaults.")
            merged[section] = copy.deepcopy(default_section)
            continue
        if not isinstance(default_section, dict):
            continue
        for key, value in values.items():
            if key not in default_section:
                warnings.append(f"{source}: unknown key '{section}.{key}' is ignored.")
            elif not _matches_type(value, default_section[key]):
                expected = type(default_section[key]).__name__
                warnings.append(
                    f"{source}: '{section}.{key}' should be of type {expected}, "
                    f"using the default {default_section[key]!r}."
                )
                merged[section][key] = copy.deepcopy(default_section[key])
    return warnings


def _read(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Configuration file '{path}' is invalid: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"Configuration file '{path}' is not a JSON object.")
    return loaded


def load_config(explicit_path: Path | None = None) -> tuple[dict, list[str]]:
    """Return ``(config, warnings)``.

    Defaults are always present; user values are merged on top recursively.
    A file passed explicitly must be usable - running with defaults the user
    did not ask for is worse than stopping - so it raises ConfigError. A broken
    file found on the search paths only produces a warning.
    """
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise ConfigError(f"Config file not found: {explicit_path}")
        loaded = _read(explicit_path)
        merged = _deep_merge(DEFAULT_CONFIG, loaded)
        warnings = _validate(merged, loaded, explicit_path)
        merged["_source"] = str(explicit_path)
        return merged, warnings

    warnings: list[str] = []
    for path in config_search_paths():
        if not path.is_file():
            continue
        try:
            loaded = _read(path)
        except ConfigError as exc:
            warnings.append(f"{exc} Using defaults.")
            continue
        merged = _deep_merge(DEFAULT_CONFIG, loaded)
        warnings += _validate(merged, loaded, path)
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
