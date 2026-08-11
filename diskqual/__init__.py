# __init__.py
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version('sirgon-diskqual')
except PackageNotFoundError:
    # Fallback for direct source execution before the package is installed.
    __version__ = '0+unknown'
