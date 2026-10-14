import json
import os
from pathlib import Path


class ConfigError(Exception):
    pass


def _load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Missing config file: {path}")
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}")


def load_config(config_path="./config.json", secrets_path="./secrets.json"):
    config_path = Path(config_path)
    secrets_path = Path(secrets_path)
    cfg = _load_json(config_path)
    secrets = {}
    try:
        secrets = _load_json(secrets_path)
    except ConfigError:
        pass
    token = os.environ.get("DISCORD_BOT_TOKEN") or secrets.get("bot_token")
    if token is None:
        raise ConfigError("Missing bot token (DISCORD_BOT_TOKEN or secrets.json)")
    cfg["_token"] = token
    return cfg
