import logging
from config import TranslationConfig

class Logger:
    """Class to handle logging configuration and operations"""
    
    def __init__(self, config: TranslationConfig):
        self.config = config
        self.logger = self._setup_logger()
        
        # Check if logger is None and provide a fallback
        if self.logger is None:
            import logging
            self.logger = logging.getLogger("fallback_logger")
            self.logger.setLevel(logging.DEBUG)
            # Add at least a console handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
    
    def _setup_logger(self):
        """Set up and return a configured logger"""
        import logging
        logger = logging.getLogger("translate_logger")
        # WARNING (not ERROR) so warnings can reach the file handler below.
        logger.setLevel(logging.DEBUG if self.config.debug_mode else logging.WARNING)

        # logging.getLogger returns the same singleton for every Logger()
        # instantiation — re-adding handlers double-logged every line and the
        # mode="w" file handler re-truncated the log mid-run. Configure once.
        if logger.handlers:
            return logger

        # Formatter for log messages
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        # File handler. WARNING floor (not ERROR) so operationally relevant
        # warnings — 529 overload waits, failed sends, skipped steps — persist
        # without needing DEBUG mode.
        file_handler = logging.FileHandler("translate.log", mode="w")  # Overwrites the file
        file_handler.setLevel(logging.DEBUG if self.config.debug_mode else logging.WARNING)
        file_handler.setFormatter(formatter)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG if self.config.debug_mode else logging.ERROR)
        console_handler.setFormatter(formatter)

        # Add handlers to the logger
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger
    
    def debug(self, message, *args, **kwargs):
        """Pass debug messages to the underlying logger"""
        self.logger.debug(message, *args, **kwargs)
    
    def info(self, message, *args, **kwargs):
        """Pass info messages to the underlying logger"""
        self.logger.info(message, *args, **kwargs)
    
    def warning(self, message, *args, **kwargs):
        """Pass warning messages to the underlying logger"""
        self.logger.warning(message, *args, **kwargs)
    
    def error(self, message, *args, **kwargs):
        """Pass error messages to the underlying logger"""
        self.logger.error(message, *args, **kwargs)
    
    def critical(self, message, *args, **kwargs):
        """Pass critical messages to the underlying logger"""
        self.logger.critical(message, *args, **kwargs)
