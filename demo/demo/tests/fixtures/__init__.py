from importlib import import_module
from pathlib import Path

for file in Path(__file__).parent.iterdir():
    if not (name := file.stem).startswith('_'):
        module = import_module(f'.{name}', __package__)
        symbols = [symbol for symbol in module.__dict__ if not symbol.startswith('_')]
        globals().update({symbol: getattr(module, symbol) for symbol in symbols})
