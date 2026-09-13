#!/usr/bin/env python3
"""Standalone launcher so a repository checkout stays runnable as ./fetch_lyrics.py.

The actual code lives in the sibling package directory ``fetchlyrics/``.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    from fetchlyrics.cli import main
except ModuleNotFoundError as exc:
    if exc.name != "fetchlyrics":
        raise

    loose = sorted(
        p.name for p in HERE.glob("*.py")
        if p.name in {"cli.py", "app.py", "providers.py", "tagging.py",
                      "text.py", "editor.py", "ui.py", "config.py"}
    )

    print("fetch-lyrics: the 'fetchlyrics' package was not found.\n", file=sys.stderr)
    print(f"Looked in: {HERE}", file=sys.stderr)

    if loose:
        print(
            "\nThe module files are sitting next to this launcher instead of inside\n"
            "a 'fetchlyrics/' directory. This happens when the files are downloaded\n"
            "individually. Fix it with:\n\n"
            f"    cd {HERE}\n"
            "    mkdir -p fetchlyrics\n"
            "    mv cli.py app.py providers.py tagging.py text.py editor.py ui.py config.py fetchlyrics/\n"
            "    touch fetchlyrics/__init__.py\n",
            file=sys.stderr,
        )
    else:
        print(
            "\nExpected this layout:\n\n"
            "    fetch_lyrics.py\n"
            "    fetchlyrics/\n"
            "        __init__.py  cli.py  app.py  providers.py\n"
            "        tagging.py   text.py editor.py ui.py config.py\n",
            file=sys.stderr,
        )
    sys.exit(2)

if __name__ == "__main__":
    sys.exit(main())
