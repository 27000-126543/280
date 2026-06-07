import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
from config.settings import LOG_CONFIG


class LoggerManager:
    _loggers = {}

    @classmethod
    def get_logger(cls, module_name):
        if module_name in cls._loggers:
            return cls._loggers[module_name]

        logger = logging.getLogger(module_name)
        logger.setLevel(getattr(logging, LOG_CONFIG["level"]))
        logger.propagate = False

        log_dir = LOG_CONFIG["log_dir"]
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, f"{module_name}.log")
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=LOG_CONFIG["max_bytes"],
            backupCount=LOG_CONFIG["backup_count"],
            encoding="utf-8",
        )

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(process)d | %(thread)d | %(message)s"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        cls._loggers[module_name] = logger
        return logger

    @classmethod
    def log_operation(cls, module, operation, user_id=None, details=None, status="success"):
        logger = cls.get_logger(module)
        log_msg = f"operation={operation} | status={status}"
        if user_id:
            log_msg += f" | user_id={user_id}"
        if details:
            log_msg += f" | details={details}"
        logger.info(log_msg)

    @classmethod
    def log_error(cls, module, operation, error, user_id=None, details=None):
        logger = cls.get_logger(module)
        log_msg = f"operation={operation} | error={str(error)}"
        if user_id:
            log_msg += f" | user_id={user_id}"
        if details:
            log_msg += f" | details={details}"
        logger.error(log_msg, exc_info=True)
