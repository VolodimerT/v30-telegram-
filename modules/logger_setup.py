import logging
import logging.handlers
from pathlib import Path
import yaml

def setup_logging(config_path: str = "bot_config.yaml"):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except:
        config = {'logging': {'level': 'INFO', 'files': {}}}
    
    log_config = config.get('logging', {})
    level = getattr(logging, log_config.get('level', 'INFO'))
    fmt = log_config.get('format', "%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    
    formatter = logging.Formatter(fmt)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    
    return root_logger

def get_logger(name: str):
    return logging.getLogger(name)
