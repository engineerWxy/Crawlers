from html.parser import HTMLParser


class MyHTMLParser(HTMLParser):
	def __init__(self):
		super().__init__()
		self.reset()
		self.text_parts = []
	
	def handle_data(self, data):
		# 收集文本数据
		self.text_parts.append(data.strip())
	
	def get_text(self):
		# 合并文本数据并返回
		return ''.join(self.text_parts)
