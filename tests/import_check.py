import pkgutil
import importlib
import sys


def main():
    """Attempt to import all submodules of the lumisync package."""
    failures = []
    import lumisync

    prefix = lumisync.__name__ + "."
    for finder, name, ispkg in pkgutil.walk_packages(lumisync.__path__, prefix):
        try:
            importlib.import_module(name)
        except Exception as e:
            failures.append((name, str(e)))

    if failures:
        print("Import failures detected:")
        for name, err in failures:
            print(name, err)
        raise SystemExit(1)
    else:
        print("All lumisync modules imported successfully")


if __name__ == "__main__":
    main()
