import logging
from logging.handlers import RotatingFileHandler


class PulseLog(logging.Logger):
	_instance = None
	FORMATTER = "%(asctime)s [%(levelname)s] [%(filename)s] [%(lineno)s] %(message)s"
	
	def __new__(cls):
		if cls._instance is None:
			cls._instance = super().__new__(cls)
		return cls._instance
	
	def __init__(self):
		super(PulseLog, self).__init__('pulse', level=logging.DEBUG)
		# file
		self.addHandler(self.file_handler())
		# console
		self.addHandler(self.console_handler())
	
	@staticmethod
	def file_handler(filename='pulse.log'):
		file_handler = RotatingFileHandler(filename, maxBytes=1024 * 1024 * 10)
		file_handler.setFormatter(logging.Formatter(PulseLog.FORMATTER, datefmt="%Y-%m-%d %H:%M:%S", style="%"))
		return file_handler
	
	@staticmethod
	def console_handler():
		console_handler = logging.StreamHandler()
		console_handler.setFormatter(logging.Formatter(PulseLog.FORMATTER, datefmt="%Y-%m-%d %H:%M:%S", style="%"))
		return console_handler


logger = PulseLog()
