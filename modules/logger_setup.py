import logging

def setup_logging(config_path: str = "bot_config.yaml"):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
    return logging.getLogger()

def get_logger(name: str):
    return logging.getLogger(name)
