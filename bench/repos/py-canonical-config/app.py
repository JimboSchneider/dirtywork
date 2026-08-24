"""app.py -- loads and lightly validates the application config."""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

REQUIRED_KEYS = ("app_name", "version", "environment", "max_connections",
                  "request_timeout_seconds", "features", "rate_limit")


def load_config(path=None):
    """Load config.json (or `path`) and return it as a dict."""
    config_path = Path(path) if path else CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")

    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError(f"config is missing required keys: {missing}")

    if not isinstance(config.get("max_connections"), int) or config["max_connections"] <= 0:
        raise ValueError("max_connections must be a positive integer")

    if not isinstance(config.get("features"), dict):
        raise ValueError("features must be an object")

    return config


def is_feature_enabled(config, name):
    """True if `name` is a truthy key under config['features']."""
    return bool(config.get("features", {}).get(name, False))


def summary(config):
    """A short human-readable description of a loaded config."""
    return f"{config['app_name']} v{config['version']} ({config['environment']})"


def _main():  # pragma: no cover
    config = load_config()
    print(summary(config))


if __name__ == "__main__":  # pragma: no cover
    _main()
