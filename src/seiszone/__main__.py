"""python -m seiszone [--minimized]                  the desktop application
python -m seiszone --engine|--probe|--add|--list|--version ...   headless (seiszone.core.cli)

Absolute imports, so this file also works as the entry script of a frozen build."""

import sys

CLI_FLAGS = ("--engine", "--probe", "--add", "--list", "--version")


def wants_cli(argv):
    """True when argv holds one of the headless options (also in the --probe=HOST:PORT form)."""
    return any(a.split("=", 1)[0] in CLI_FLAGS for a in argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if wants_cli(argv):
        from seiszone.core import cli
        return cli.main(argv)
    from seiszone.ui import app
    return app.main(argv)


if __name__ == "__main__":
    sys.exit(main())
