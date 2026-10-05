#!/usr/bin/env python3
"""Standalone launcher so a repository checkout stays runnable as ./run.py.

The actual code lives in the sibling package directory ``lrckit/``. The
launcher must not be called ``lrckit.py``: without the package directory,
``import lrckit`` would then import this file itself instead of failing.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    from lrckit.cli import main
except ModuleNotFoundError as exc:
    if exc.name != "lrckit":
        raise

    loose = sorted(
        p.name for p in HERE.glob("*.py")
        if p.name in {"cli.py", "app.py", "providers.py", "tagging.py",
                      "text.py", "editor.py", "ui.py", "config.py"}
    )

    print("lrckit: the 'lrckit' package was not found.\n", file=sys.stderr)
    print(f"Looked in: {HERE}", file=sys.stderr)

    if loose:
        print(
            "\nThe module files are sitting next to this launcher instead of inside\n"
            "a 'lrckit/' directory. This happens when the files are downloaded\n"
            "individually. Fix it with:\n\n"
            f"    cd {HERE}\n"
            "    mkdir -p lrckit\n"
            "    mv cli.py app.py providers.py tagging.py text.py editor.py ui.py config.py lrckit/\n"
            "    touch lrckit/__init__.py\n",
            file=sys.stderr,
        )
    else:
        print(
            "\nExpected this layout:\n\n"
            "    run.py\n"
            "    lrckit/\n"
            "        __init__.py  cli.py  app.py  providers.py\n"
            "        tagging.py   text.py editor.py ui.py config.py\n",
            file=sys.stderr,
        )
    sys.exit(2)

if __name__ == "__main__":
    sys.exit(main())
