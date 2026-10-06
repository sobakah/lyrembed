"""Configuration loading with recursive merging of user overrides."""

from __future__ import annotations

import copy
import json
import logging
import os
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__version__ = "0.5.0"
PROJECT_URL = "https://github.com/sobakah/lrckit"

DEFAULT_CONFIG: dict[str, Any] = {
    # Extra groups from the user; the built-in ones live in aliases.json.
    "aliases": [],
    "cleaning": {
        "feature_regex": r"[\(\[][\s]*(?:feat\.?|featuring|ft\.?)\s+[^\)\]]+[\)\]]",
        "trailing_feature_regex": r"\s+(?:feat\.?|featuring|ft\.?)\s+.*$",
        "korean_bracket_regex": r"[\(\[][^\)\]]*[가-힣ㄱ-ㅎㅏ-ㅣ][^\)\]]*[\)\]]",
        "japanese_bracket_regex": r"[\(\[][^\)\]]*[ぁ-ゖァ-ヺ一-龥][^\)\]]*[\)\]]",
        "chinese_bracket_regex": r"[\(\[][^\)\]]*[一-龥][^\)\]]*[\)\]]",
        "ignore_words_artist_match": ["the", "and", "feat", "ft", "with", "&"],
        "strip_all_brackets": False,
    },
    "api": {
        "lrclib_url": "https://lrclib.net/api",
        "netease_search_url": "https://music.163.com/api/search/pc",
        "netease_lyric_url": "https://music.163.com/api/song/lyric",
        "timeout_seconds": 6,
        "netease_search_limit": 6,
        "user_agent": f"lrckit/{__version__} ({PROJECT_URL})",
        "max_workers": 8,
        "retry_total": 2,
        "retry_backoff": 0.3,
        "duration_tolerance_seconds": 2,
        "syncedlyrics_timeout_seconds": 15,
        "syncedlyrics_as_fallback": True,
        "syncedlyrics_providers": ["Musixmatch", "Megalobiz", "Genius"],
        "batch_delay_seconds": 1.0,
        "enable_lrclib": True,
        "enable_netease": True,
        "enable_syncedlyrics": True,
        "enable_musicbrainz": False,
        "musicbrainz_url": "https://musicbrainz.org/ws/2",
    },
    "settings": {
        "supported_extensions": [".flac", ".mp3", ".ogg", ".opus", ".m4a"],
        "default_editor": "nano",
        "preview_lines": 16,
        "non_latin_ratio_threshold": 0.1,
        "max_search_depth": 3,
        "max_file_count": 250,
        "search_cache_entries": 64,
        "prefer_latin": False,
        "prefer_synced": True,
        "sort_by_tags": False,
        "write_sidecar_lrc": False,
        "id3_v2_version": 4,
        "uslt_language": "eng",
        "romanization_layout": "replace",
        "prefer_album_artist": False,
        "search_all_aliases": False,
        "color": "auto",
    },
}

# What each setting does, written next to it into config.example.json.
SETTING_HELP: dict[str, Any] = {
    "aliases": (
        "Your own artist alias groups, added to the built-in ones in "
        "lrckit/aliases.json (groups sharing a name are merged). Each group lists "
        "names of one artist, e.g. [[\"Hoshino Gen\", \"星野源\"]]."
    ),
    "cleaning": {
        "feature_regex": "Removes '(feat. X)' / '[ft. X]' from titles and artists before searching.",
        "trailing_feature_regex": "Removes a trailing ' feat. X' without brackets.",
        "korean_bracket_regex": "Removes bracketed Korean subtitles, e.g. 'Love Scenario (사랑을 했다)'.",
        "japanese_bracket_regex": "Removes bracketed Japanese subtitles.",
        "chinese_bracket_regex": "Removes bracketed Chinese subtitles.",
        "ignore_words_artist_match": "Words ignored when comparing artist names word by word.",
        "strip_all_brackets": (
            "true removes everything in (...) and [...] from title, artist and album "
            "before searching, e.g. member lists: 'Group (\"A\", \"B\")'. Also drops '(Live)'."
        ),
    },
    "api": {
        "lrclib_url": "LRCLIB API base URL.",
        "netease_search_url": "NetEase search endpoint.",
        "netease_lyric_url": "NetEase lyrics endpoint.",
        "timeout_seconds": "Timeout per HTTP request.",
        "netease_search_limit": "How many NetEase songs to fetch lyrics for per search.",
        "max_workers": "Parallel requests per search.",
        "retry_total": "Retries for failed requests.",
        "retry_backoff": "Backoff factor between retries, in seconds.",
        "duration_tolerance_seconds": "Allowed length difference for a confident (automatic) match.",
        "syncedlyrics_timeout_seconds": "How long to wait for the syncedlyrics scrapers.",
        "syncedlyrics_as_fallback": (
            "true runs the syncedlyrics scrapers only when LRCLIB and NetEase find no synced "
            "lyrics that fit; false runs them with every search."
        ),
        "syncedlyrics_providers": (
            "Sources syncedlyrics may use, in its own order. LRCLIB and NetEase are left out because "
            "lrckit asks them directly; [] lets syncedlyrics use all of its sources."
        ),
        "batch_delay_seconds": "Pause between tracks in batch mode, to go easy on LRCLIB.",
        "enable_lrclib": "Search LRCLIB.",
        "enable_netease": "Search NetEase.",
        "enable_syncedlyrics": "Search via syncedlyrics (needs the optional package).",
        "enable_musicbrainz": (
            "true asks MusicBrainz whether a track is instrumental, for files tagged with a "
            "MusicBrainz recording ID (Picard). One extra request per track, at most one per second."
        ),
        "musicbrainz_url": "MusicBrainz API base URL.",
    },
    "settings": {
        "supported_extensions": "File types to scan; can only narrow the five supported ones.",
        "default_editor": "Editor when $VISUAL and $EDITOR are unset.",
        "preview_lines": "Lines shown in the short lyrics preview.",
        "non_latin_ratio_threshold": "Share of lyric lines with non-Latin letters above which lyrics count as original script (0.1 = 10%).",
        "max_search_depth": "Directory levels to scan below the start folder.",
        "max_file_count": "Stop the scan above this many files; 0 = no limit.",
        "search_cache_entries": "Searches kept in memory per session.",
        "prefer_latin": "true ranks romanized lyrics first; false leaves the script out of the ranking.",
        "prefer_synced": "true ranks synced lyrics above plain ones, even above an exact match with plain lyrics only.",
        "sort_by_tags": "true sorts by disc/track number, false by file name.",
        "write_sidecar_lrc": "Also write a .lrc file next to each track.",
        "id3_v2_version": "ID3v2 version (3 or 4) for MP3s without a tag; existing tags keep theirs.",
        "uslt_language": "Three-letter language code stored with MP3 lyrics.",
        "romanization_layout": "What Enter picks after romanizing: \"replace\" or \"below\" (keep the original line).",
        "prefer_album_artist": "true searches with the album artist instead of the track artist.",
        "search_all_aliases": (
            "true searches every alias of the artist right away. false (default) searches the "
            "tag's artist first and the aliases only when that finds no synced lyrics, or on 'a'."
        ),
        "color": "\"auto\", \"always\" or \"never\".",
    },
}

EXAMPLE_ABOUT = [
    "lrckit does NOT read this file. It lists every setting with its built-in default.",
    "To change settings, create ~/.config/lrckit/config.json (or pass --config FILE)",
    "with only the keys you want to change; everything else keeps its default. Example:",
    "{ \"settings\": { \"prefer_latin\": true }, \"cleaning\": { \"strip_all_brackets\": true } }",
    "Keys starting with an underscore, like this one, are comments and ignored.",
    "Built-in artist aliases are in lrckit/aliases.json; add your own under \"aliases\".",
    "lrckit --list-aliases shows every alias group in effect.",
]


def example_config() -> dict:
    """The content of config.example.json: defaults with their explanations.

    Each value is preceded by an '_<key>' comment, which load_config ignores,
    so a copied example still works. api.user_agent is left out: it is derived
    from the version, and a copied value would freeze it.
    """
    example: dict[str, Any] = {"_about": EXAMPLE_ABOUT}
    for section, default in DEFAULT_CONFIG.items():
        help_text = SETTING_HELP.get(section)
        if not isinstance(default, dict):
            example[f"_{section}"] = help_text
            example[section] = copy.deepcopy(default)
            continue
        entries: dict[str, Any] = {}
        for key, value in default.items():
            if section == "api" and key == "user_agent":
                continue
            entries[f"_{key}"] = help_text[key]
            entries[key] = copy.deepcopy(value)
        example[section] = entries
    return example


def write_example(path: Path) -> None:
    path.write_text(json.dumps(example_config(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _strip_comments(value: Any) -> Any:
    """Drop '_'-prefixed keys: comments in the example config."""
    if isinstance(value, dict):
        return {k: _strip_comments(v) for k, v in value.items() if not str(k).startswith("_")}
    return value


# --- artist aliases ----------------------------------------------------------
def builtin_aliases_path():
    return resources.files(__package__).joinpath("aliases.json")


@lru_cache(maxsize=1)
def builtin_aliases() -> tuple[tuple[str, ...], ...]:
    """The groups in lrckit/aliases.json, read once.

    Kept in a data file rather than in code, so what is in effect can be read
    and extended without reading Python.
    """
    try:
        raw = builtin_aliases_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        log.error("Built-in artist aliases could not be loaded: %s", exc)
        return ()
    groups = []
    for key, value in data.items():
        if key.startswith("_") or not isinstance(value, list):
            continue
        groups.extend(tuple(str(name) for name in group) for group in value if isinstance(group, list))
    return tuple(groups)


@lru_cache(maxsize=8)
def _merged_aliases(user: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
    """Built-in plus user groups; groups that share a name become one, so a
    user group extending a built-in one does not split the artist in two."""
    merged: list[list[str]] = []
    for group in builtin_aliases() + user:
        names = [name for name in group if name.strip()]
        lowered = {name.lower() for name in names}
        overlapping = [g for g in merged if lowered & {name.lower() for name in g}]
        combined = [name for g in overlapping for name in g]
        for name in names:
            if name.lower() not in {c.lower() for c in combined}:
                combined.append(name)
        merged = [g for g in merged if g not in overlapping] + [combined]
    return tuple(tuple(g) for g in merged if len(g) > 1)


def alias_groups() -> tuple[tuple[str, ...], ...]:
    user = CONFIG.get("aliases", [])
    if not isinstance(user, list):
        user = []
    user_groups = tuple(
        tuple(str(name) for name in group) for group in user if isinstance(group, list)
    )
    return _merged_aliases(user_groups)


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
    return _strip_comments(loaded)


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
