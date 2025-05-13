"""
Logging utilities for OPC-UA Gateway
"""
import logging
import os
import sys
from datetime import datetime


def setup_logger(name, log_level=logging.INFO, log_to_file=False, log_dir='logs'):
    """Set up a logger with console and optional file output"""
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.handlers = []  # Clear any existing handlers

    # Create formatter
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s')

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_to_file:
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        log_file = os.path.join(
            log_dir,
            f"{name.lower().replace('.', '_')}_{datetime.now().strftime('%Y%m%d')}.log"
        )

        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_log_level(level_str):
    """Convert string log level to logging constant"""
    levels = {
        'DEBUG': logging.DEBUG,
        'INFO': logging.INFO,
        'WARNING': logging.WARNING,
        'ERROR': logging.ERROR,
        'CRITICAL': logging.CRITICAL
    }
    return levels.get(level_str.upper(), logging.INFO)