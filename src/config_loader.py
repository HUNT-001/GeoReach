"""Configuration loader for GeoReach."""
import os
import yaml

def load_config(config_path=None):
    """Load project configuration from YAML file."""
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "settings.yaml"
        )
    with open(config_path, "r") as f:
        return yaml.safe_load(f)

# Singleton config
_config = None

def get_config():
    """Get cached configuration."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
