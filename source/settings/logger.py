import copy
import inspect
import logging
import os
import threading
from threading import Lock

from colorama import Fore, Style


class Logger:
    """
    Logger with console output and log file saving:

    Parameters:
    ------------
    `name: Logger name (auto-determined but if you want to set it manually, you can)`
    `level: Log level`
    `log_dir: Directory for saving logs`
    `log_filename: Log file name`

    Usage:
    ------------
    To initialize the logger, you need to create an instance of the Logger class::

        logger = Logger(level="DEBUG")

    To output logs, you need to use the methods::

        logger.info("Hello, world!")
        logger.debug("Debug message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.critical("Critical message")
    """

    # Shared state for all instances (for fork - individual state)
    _state = {}
    _lock = Lock()

    def __init__(
        self,
        name: str = "auto",
        level: str = "DEBUG",
        output_dir: str = "outputs",
        output_filename: str = "info.log",
    ):
        # Auto-determination of the logger name
        if name == "auto":
            name = self.__get_logger_name()

        # Configuration of the logger (name is not included)
        cfg = {
            "name": name,
            "level": level,
            "output_dir": output_dir,
            "output_filename": output_filename,
            "output_file": os.path.join(output_dir, output_filename),
        }

        # Getting the class of the instance
        cls = type(self)

        # Locking the state for the first and subsequent instances
        with cls._lock:
            if not cls._state:
                # First instance: fix state
                cls._state = cfg.copy()
                self.__dict__ = cls._state
            else:
                # Check for differences (including name)
                if any(cfg[k] != cls._state.get(k) for k in list(cfg.keys())):
                    # Fork: individual state
                    self.__dict__ = cls._state.copy()
                    self.__dict__.update(cfg)
                else:
                    # Same - common state
                    self.__dict__ = cls._state

        # Initialize the logger
        os.makedirs(self.output_dir, exist_ok=True)
        self.__initialize_logger()

    def __initialize_logger(self):
        """
        Logger initialization
        Returns:
            Logger: Logger
        """
        # Configuration of the format
        FORMAT_FOR_FILE = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
        DATEFMT = "%Y-%m-%d %H:%M:%S"

        # Creating formatters
        formatter = ColoredFormatter(datefmt=DATEFMT)

        # File handler with UTF-8 encoding for compatibility
        file_handler = logging.FileHandler(self.output_file, encoding="utf-8")
        file_handler.setLevel(getattr(logging, self.level.upper()))
        file_handler.setFormatter(PrefixedFormatter(fmt=FORMAT_FOR_FILE, datefmt=DATEFMT))

        # Console handler
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(getattr(logging, self.level.upper()))
        stream_handler.setFormatter(formatter)

        # Configure the root logger
        logging.basicConfig(
            level=getattr(logging, self.level.upper()),
            handlers=[file_handler, stream_handler],
            force=True,
        )

        # Set specific loggers to higher level to suppress their output
        logging.getLogger("web3.providers.HTTPProvider").setLevel(logging.WARNING)

        # Creating the logger
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(self.level)

        return self.logger

    def debug(self, msg):
        self.logger.debug(msg)

    def info(self, msg):
        self.logger.info(msg)

    def warning(self, msg):
        self.logger.warning(msg)

    def error(self, msg):
        self.logger.error(msg)

    def critical(self, msg):
        self.logger.critical(msg)

    def __get_logger_name(self):
        """
        Determines the logger name based on the path of the calling file
        """

        frame = inspect.currentframe()
        try:
            # Go up the call stack to find the file that creates the logger
            caller_frame = frame.f_back.f_back  # Skip __init__ and call Logger()
            if caller_frame is None:
                return "root"

            caller_file = caller_frame.f_globals.get("__file__")
            if not caller_file:
                return "root"

            # Get the absolute path and normalize
            caller_path = os.path.abspath(caller_file)

            # Determine the project root by finding the directory containing main.py
            def find_project_root(start_path):
                current = start_path
                while current != os.path.dirname(current):  # Stop at root directory
                    if os.path.exists(os.path.join(current, "main.py")):
                        return current
                    current = os.path.dirname(current)
                return None

            # Start from the caller's directory
            caller_dir = os.path.dirname(caller_path)
            project_root = find_project_root(caller_dir)

            if not project_root:
                # Fallback: use parent of source directory
                current_dir = os.path.dirname(os.path.abspath(__file__))  # source/settings
                source_dir = os.path.dirname(current_dir)  # source
                project_root = os.path.dirname(source_dir)  # project root

            # Check if the file main.py is in the project root
            if os.path.basename(caller_file) == "main.py" and os.path.dirname(caller_path) == project_root:
                return "root/main.py"

            # Get the relative path from the project root
            try:
                rel_path = os.path.relpath(caller_path, project_root)
                path_parts = rel_path.replace("\\", "/").split("/")

                # If the file is in a subfolder, include the full path with the file name
                if len(path_parts) > 1 and path_parts[0] != ".":
                    return "root/" + "/".join(path_parts)
                elif len(path_parts) == 1:
                    return "root/" + path_parts[0]

            except ValueError:
                # If we can't get the relative path
                pass

            return "root"

        finally:
            del frame


class ColoredFormatter(logging.Formatter):
    """
    Formatter for colored output of logs, auxiliary class for the logger
    """

    colors = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        record_copy = copy.copy(record)

        # Colors of values by keys
        COLORS = {
            "timestamp": Fore.MAGENTA,
            "name": "\033[38;5;19m",
            "levelname": {
                "INFO": Fore.LIGHTWHITE_EX,
                "DEBUG": Fore.CYAN,
                "WARNING": Fore.YELLOW,
                "ERROR": Fore.LIGHTRED_EX,
                "CRITICAL": Fore.RED + Style.BRIGHT,
            },
        }

        # Colors of brackets and keys
        bracket_color = Fore.LIGHTBLACK_EX
        key_color = Fore.LIGHTBLACK_EX

        # Time format
        timestamp = self.formatTime(record_copy, self.datefmt)

        # Build the log string manually
        level_raw = record_copy.levelname
        level_padded = level_raw
        level_color = COLORS["levelname"].get(level_raw, Fore.WHITE)
        msg_color = level_color

        # Thread-local prefix support
        try:
            prefix = getattr(_TLS, "prefix", "")
        except Exception:
            prefix = ""

        message_text = record_copy.getMessage()
        if prefix:
            message_text = f"{prefix} {message_text}"

        formatter = " ".join(
            [
                f"{bracket_color}{key_color}{COLORS['timestamp']}{timestamp}{bracket_color} |",
                f"{bracket_color}{key_color}{COLORS['name']}{record_copy.name}{bracket_color} |",
                f"{bracket_color}{key_color}{level_color}{level_padded}{bracket_color} |",
                f"{bracket_color}{Style.RESET_ALL} {msg_color}{message_text}{Style.RESET_ALL}",
            ]
        )

        return formatter


# Thread-local storage for per-thread log prefixes
_TLS = threading.local()


def set_log_prefix(prefix: str = ""):
    """
    Set a thread-local log prefix (e.g., "[T1][W2]") to be included before the message.
    """
    try:
        _TLS.prefix = prefix or ""
    except Exception:
        _TLS.prefix = ""


class PrefixedFormatter(logging.Formatter):
    """
    Plain text formatter that injects thread-local prefix before the message
    so both console and file logs share the same prefix logic.
    """

    def format(self, record):
        try:
            prefix = getattr(_TLS, "prefix", "")
        except Exception:
            prefix = ""
        original_msg = record.getMessage()
        if prefix:
            record.message = f"{prefix} {original_msg}"
        else:
            record.message = original_msg
        # Emulate logging.Formatter.format behavior with record.message
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        s = self._fmt.replace("%(message)s", record.message)
        try:
            return s % record.__dict__
        except Exception:
            # Fallback to default behavior
            return super().format(record)
