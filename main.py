import sys
import random
import json
import codecs
import csv
import math
import requests
from lxml import etree
from tqdm import tqdm
import os
from time import sleep
from requests.adapters import HTTPAdapter

from datetime import date, datetime, timedelta

from util import csvutil
from util.log import logger
from util.html_parser import MyHTMLParser

DATEFORMAT = "%Y-%m-%dT%H:%M:%S"


class WeiBoSpider(object):
	def __init__(self, config):
		"""Weibo类初始化"""
		self.validate_config(config)
		self.only_crawl_original = config["only_crawl_original"]  # 取值范围为0、1,程序默认值为0,代表要爬取用户的全部微博,1代表只爬取用户的原创微博
		self.remove_html_tag = config[
			"remove_html_tag"
		]  # 取值范围为0、1, 0代表不移除微博中的html tag, 1代表移除
		since_date = config["since_date"]
		# since_date 若为整数，则取该天数之前的日期；若为 yyyy-mm-dd，则增加时间
		if isinstance(since_date, int):
			since_date = date.today() - timedelta(since_date)
			since_date = since_date.strftime(DATEFORMAT)
		elif WeiBoSpider.is_date(since_date):
			since_date = "{}T00:00:00".format(since_date)
		elif WeiBoSpider.is_datetime(since_date):
			pass
		else:
			logger.error("since_date 格式不正确，请确认配置是否正确")
			sys.exit()
		self.since_date = since_date  # 起始时间，即爬取发布日期从该值到现在的微博，形式为yyyy-mm-ddThh:mm:ss，如：2023-08-21T09:23:03
		self.start_page = config.get("start_page", 1)  # 开始爬的页，如果中途被限制而结束可以用此定义开始页码
		self.write_mode = config[
			"write_mode"
		]  # 结果信息保存类型，为list形式，可包含csv、mongo和mysql三种类型
		self.original_pic_download = config[
			"original_pic_download"
		]  # 取值范围为0、1, 0代表不下载原创微博图片,1代表下载
		self.retweet_pic_download = config[
			"retweet_pic_download"
		]  # 取值范围为0、1, 0代表不下载转发微博图片,1代表下载
		self.original_video_download = config[
			"original_video_download"
		]  # 取值范围为0、1, 0代表不下载原创微博视频,1代表下载
		self.retweet_video_download = config[
			"retweet_video_download"
		]  # 取值范围为0、1, 0代表不下载转发微博视频,1代表下载
		self.download_comment = config["download_comment"]  # 1代表下载评论,0代表不下载
		self.comment_max_download_count = config[
			"comment_max_download_count"
		]  # 如果设置了下评论，每条微博评论数会限制在这个值内
		self.download_repost = config["download_repost"]  # 1代表下载转发,0代表不下载
		self.repost_max_download_count = config[
			"repost_max_download_count"
		]  # 如果设置了下转发，每条微博转发数会限制在这个值内
		self.user_id_as_folder_name = config.get(
			"user_id_as_folder_name", 0
		)  # 结果目录名，取值为0或1，决定结果文件存储在用户昵称文件夹里还是用户id文件夹里
		cookie = config.get("cookie")  # 微博cookie，可填可不填
		user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.111 Safari/537.36"
		self.headers = {"User_Agent": user_agent, "Cookie": cookie}
		self.mysql_config = config.get("mysql_config")  # MySQL数据库连接配置，可以不填
		self.mongodb_URI = config.get("mongodb_URI")  # MongoDB数据库连接字符串，可以不填
		self.post_config = config.get("post_config")  # post_config，可以不填
		user_id_list = config["user_id_list"]
		# 避免卡住
		if isinstance(user_id_list, list):
			random.shuffle(user_id_list)
		
		query_list = config.get("query_list") or []
		if isinstance(query_list, str):
			query_list = query_list.split(",")
		self.query_list = query_list
		self.user_config_file_path = ""
		user_config_list = [
			{
				"user_id": user_id,
				"since_date": self.since_date,
				"query_list": query_list,
			}
			for user_id in user_id_list
		]
		
		self.user_config_list = user_config_list  # 要爬取的微博用户的user_config列表
		self.user_config = {}  # 用户配置,包含用户id和since_date
		self.start_date = ""  # 获取用户第一条微博时的日期
		self.query = ""
		self.user = {}  # 存储目标微博用户信息
		self.got_count = 0  # 存储爬取到的微博数
		self.weibo = []  # 存储爬取到的所有微博信息
		self.weibo_id_list = []  # 存储爬取到的所有微博id
		self.long_sleep_count_before_each_user = 0  # 每个用户前的长时间sleep避免被ban
	
	def validate_config(self, config):
		"""验证配置是否正确"""
		
		# 验证如下1/0相关值
		argument_list = [
			"only_crawl_original",
			"original_pic_download",
			"retweet_pic_download",
			"original_video_download",
			"retweet_video_download",
			"download_comment",
			"download_repost",
		]
		for argument in argument_list:
			if config[argument] != 0 and config[argument] != 1:
				logger.warning("%s值应为0或1,请重新输入", config[argument])
				sys.exit()
		
		# 验证query_list
		query_list = config.get("query_list") or []
		if not isinstance(query_list, list):
			logger.warning("query_list值应为list类型或字符串,请重新输入")
			sys.exit()
		
		# 验证write_mode
		write_mode = ["csv", "json", "mongo", "mysql", "post"]
		if not isinstance(config["write_mode"], list):
			sys.exit("write_mode值应为list类型")
		for mode in config["write_mode"]:
			if mode not in write_mode:
				logger.warning(
					"%s为无效模式，请从csv、json、mongo和mysql中挑选一个或多个作为write_mode", mode
				)
				sys.exit()
		
		# 验证user_id_list
		user_id_list = config["user_id_list"]
		if not isinstance(user_id_list, list):
			logger.warning("user_id_list值应为list类型")
			sys.exit()
		
		# 验证since_date
		since_date = config["since_date"]
		if (not isinstance(since_date, int)) and (not self.is_datetime(since_date)) and (not self.is_date(since_date)):
			logger.warning("since_date值应为yyyy-mm-dd形式、yyyy-mm-ddTHH:MM:SS形式或整数，请重新输入")
			sys.exit()
		
		comment_max_count = config["comment_max_download_count"]
		if not isinstance(comment_max_count, int):
			logger.warning("最大下载评论数 (comment_max_download_count) 应为整数类型")
			sys.exit()
		elif comment_max_count < 0:
			logger.warning("最大下载评论数 (comment_max_download_count) 应该为正整数")
			sys.exit()
		
		repost_max_count = config["repost_max_download_count"]
		if not isinstance(repost_max_count, int):
			logger.warning("最大下载转发数 (repost_max_download_count) 应为整数类型")
			sys.exit()
		elif repost_max_count < 0:
			logger.warning("最大下载转发数 (repost_max_download_count) 应该为正整数")
			sys.exit()
	
	@staticmethod
	def is_date(since_date):
		"""判断日期格式是否为 %Y-%m-%d"""
		try:
			datetime.strptime(since_date, "%Y-%m-%d")
			return True
		except ValueError:
			return False
	
	@staticmethod
	def is_datetime(since_date):
		"""判断日期格式是否为 %Y-%m-%dT%H:%M:%S"""
		try:
			datetime.strptime(since_date, DATEFORMAT)
			return True
		except ValueError:
			return False
	
	@staticmethod
	def string_to_int(string):
		"""字符串转换为整数"""
		if isinstance(string, int):
			return string
		elif string.endswith("万+"):
			string = string[:-2] + "0000"
		elif string.endswith("万"):
			string = float(string[:-1]) * 10000
		elif string.endswith("亿"):
			string = float(string[:-1]) * 100000000
		return int(string)
	
	def initialize_info(self, user_config):
		"""初始化爬虫信息"""
		self.weibo = []
		self.user = {}
		self.user_config = user_config
		self.got_count = 0
		self.weibo_id_list = []
	
	@staticmethod
	def standardize_info(weibo):
		"""标准化信息，去除乱码"""
		for k, v in weibo.items():
			if (
				"bool" not in str(type(v))
				and "int" not in str(type(v))
				and "list" not in str(type(v))
				and "long" not in str(type(v))
			):
				weibo[k] = (
					v.replace("\u200b", "")
					.encode(sys.stdout.encoding, "ignore")
					.decode(sys.stdout.encoding)
				)
		return weibo
	
	def get_json(self, params):
		"""获取网页中json数据"""
		url = "https://m.weibo.cn/api/container/getIndex?"
		r = requests.get(url, params=params, headers=self.headers, verify=False)
		return r.json(), r.status_code
	
	def get_user_info(self):
		"""获取用户信息"""
		params = {"containerid": "100505" + str(self.user_config["user_id"])}
		
		# 这里在读取下一个用户的时候很容易被ban，需要优化休眠时长
		# 加一个count，不需要一上来啥都没干就sleep
		if self.long_sleep_count_before_each_user > 0:
			sleep_time = random.randint(30, 60)
			# 添加log，否则一般用户不知道以为程序卡了
			logger.info(f"""短暂sleep {sleep_time}秒，避免被ban""")
			sleep(sleep_time)
			logger.info("sleep结束")
		self.long_sleep_count_before_each_user = self.long_sleep_count_before_each_user + 1
		
		js, status_code = self.get_json(params)
		if status_code != 200:
			logger.info("被ban了，需要等待一段时间")
			sys.exit()
		if js["ok"]:
			info = js["data"]["userInfo"]
			user_info = dict()
			user_info["id"] = self.user_config["user_id"]
			user_info["screen_name"] = info.get("screen_name", "")
			user_info["gender"] = info.get("gender", "")
			params = {
				"containerid": "230283" + str(self.user_config["user_id"]) + "_-_INFO"
			}
			zh_list = ["生日", "所在地", "小学", "初中", "高中", "大学", "公司", "注册时间", "阳光信用"]
			en_list = [
				"birthday",
				"location",
				"education",
				"education",
				"education",
				"education",
				"company",
				"registration_time",
				"sunshine",
			]
			for i in en_list:
				user_info[i] = ""
			js, _ = self.get_json(params)
			if js["ok"]:
				cards = js["data"]["cards"]
				if isinstance(cards, list) and len(cards) > 1:
					card_list = cards[0]["card_group"] + cards[1]["card_group"]
					for card in card_list:
						if card.get("item_name") in zh_list:
							user_info[
								en_list[zh_list.index(card.get("item_name"))]
							] = card.get("item_content", "")
			user_info["statuses_count"] = WeiBoSpider.string_to_int(
				info.get("statuses_count", 0)
			)
			user_info["followers_count"] = WeiBoSpider.string_to_int(
				info.get("followers_count", 0)
			)
			user_info["follow_count"] = WeiBoSpider.string_to_int(info.get("follow_count", 0))
			user_info["description"] = info.get("description", "")
			user_info["profile_url"] = info.get("profile_url", "")
			user_info["profile_image_url"] = info.get("profile_image_url", "")
			user_info["avatar_hd"] = info.get("avatar_hd", "")
			user_info["urank"] = info.get("urank", 0)
			user_info["mbrank"] = info.get("mbrank", 0)
			user_info["verified"] = info.get("verified", False)
			user_info["verified_type"] = info.get("verified_type", -1)
			user_info["verified_reason"] = info.get("verified_reason", "")
			user = WeiBoSpider.standardize_info(user_info)
			self.user = user
			self.user_to_database()
			return 0
		else:
			logger.info("user_id_list中 {} id出错".format(self.user_config["user_id"]))
			return -1
	
	def user_to_database(self):
		"""将用户信息写入文件/数据库"""
		self.user_to_csv()
	
	# if "mysql" in self.write_mode:
	# 	self.user_to_mysql()
	# if "mongo" in self.write_mode:
	# 	self.user_to_mongodb()
	
	def user_to_csv(self):
		"""将爬取到的用户信息写入csv文件"""
		file_dir = os.path.split(os.path.realpath(__file__))[0] + os.sep + "weibo"
		if not os.path.isdir(file_dir):
			os.makedirs(file_dir)
		file_path = file_dir + os.sep + "users.csv"
		self.user_csv_file_path = file_path
		result_headers = [
			"用户id",
			"昵称",
			"性别",
			"生日",
			"所在地",
			"学习经历",
			"公司",
			"注册时间",
			"阳光信用",
			"微博数",
			"粉丝数",
			"关注数",
			"简介",
			"主页",
			"头像",
			"高清头像",
			"微博等级",
			"会员等级",
			"是否认证",
			"认证类型",
			"认证信息",
			"上次记录微博信息",
		]
		result_data = [
			[
				v.encode("utf-8") if "unicode" in str(type(v)) else v
				for v in self.user.values()
			]
		]
		# 已经插入信息的用户无需重复插入，返回的id是空字符串或微博id 发布日期%Y-%m-%d
		last_weibo_msg = csvutil.insert_or_update_user(
			logger, result_headers, result_data, file_path
		)
		self.last_weibo_id = last_weibo_msg.split(" ")[0] if last_weibo_msg else ""
		self.last_weibo_date = (
			last_weibo_msg.split(" ")[1]
			if last_weibo_msg
			else self.user_config["since_date"]
		)
	
	def get_page_count(self):
		"""获取微博页数"""
		try:
			weibo_count = self.user["statuses_count"]
			page_count = int(math.ceil(weibo_count / 10.0))
			return page_count
		except KeyError:
			logger.exception(
				"程序出错，错误原因可能为以下两者：\n"
				"1.user_id不正确；\n"
				"2.此用户微博可能需要设置cookie才能爬取。\n"
				"解决方案：\n"
				"请参考\n"
				"https://github.com/dataabc/weibo-crawler#如何获取user_id\n"
				"获取正确的user_id；\n"
				"或者参考\n"
				"https://github.com/dataabc/weibo-crawler#3程序设置\n"
				"中的“设置cookie”部分设置cookie信息"
			)
	
	def get_weibo_json(self, page):
		"""获取网页中微博json数据"""
		params = (
			{
				"container_ext": "profile_uid:" + str(self.user_config["user_id"]),
				"containerid": "100103type=401&q=" + self.query,
				"page_type": "searchall",
			}
			if self.query
			else {"containerid": "230413" + str(self.user_config["user_id"])}
		)
		params["page"] = page
		js, _ = self.get_json(params)
		return js
	
	def get_pics(self, weibo_info):
		"""获取微博原始图片url"""
		if weibo_info.get("pics"):
			pic_info = weibo_info["pics"]
			pic_list = [pic["large"]["url"] for pic in pic_info]
			pics = ",".join(pic_list)
		else:
			pics = ""
		return pics
	
	def get_live_photo(self, weibo_info):
		"""获取live photo中的视频url"""
		live_photo_list = weibo_info.get("live_photo", [])
		return live_photo_list
	
	def get_video_url(self, weibo_info):
		"""获取微博视频url"""
		video_url = ""
		video_url_list = []
		if weibo_info.get("page_info"):
			if (
				weibo_info["page_info"].get("urls")
				or weibo_info["page_info"].get("media_info")
			) and weibo_info["page_info"].get("type") == "video":
				media_info = weibo_info["page_info"]["urls"]
				if not media_info:
					media_info = weibo_info["page_info"]["media_info"]
				video_url = media_info.get("mp4_720p_mp4")
				if not video_url:
					video_url = media_info.get("mp4_hd_url")
				if not video_url:
					video_url = media_info.get("hevc_mp4_hd")
				if not video_url:
					video_url = media_info.get("mp4_sd_url")
				if not video_url:
					video_url = media_info.get("mp4_ld_mp4")
				if not video_url:
					video_url = media_info.get("stream_url_hd")
				if not video_url:
					video_url = media_info.get("stream_url")
		if video_url:
			video_url_list.append(video_url)
		live_photo_list = self.get_live_photo(weibo_info)
		if live_photo_list:
			video_url_list += live_photo_list
		return ";".join(video_url_list)
	
	def get_article_url(self, selector):
		"""获取微博中头条文章的url"""
		article_url = ""
		text = selector.xpath("string(.)")
		if text.startswith("发布了头条文章"):
			url = selector.xpath("//a/@data-url")
			if url and url[0].startswith("http://t.cn"):
				article_url = url[0]
		return article_url
	
	def get_location(self, selector):
		"""获取微博发布位置"""
		location_icon = "timeline_card_small_location_default.png"
		span_list = selector.xpath("//span")
		location = ""
		for i, span in enumerate(span_list):
			if span.xpath("img/@src"):
				if location_icon in span.xpath("img/@src")[0]:
					location = span_list[i + 1].xpath("string(.)")
					break
		return location
	
	def parse_weibo(self, weibo_info):
		weibo = dict()
		if weibo_info["user"]:
			weibo["user_id"] = weibo_info["user"]["id"]
			weibo["screen_name"] = weibo_info["user"]["screen_name"]
		else:
			weibo["user_id"] = ""
			weibo["screen_name"] = ""
		weibo["id"] = int(weibo_info["id"])
		weibo["bid"] = weibo_info["bid"]
		text_body = weibo_info["text"]
		selector = etree.HTML(f"{text_body}<hr>" if text_body.isspace() else text_body)
		if self.remove_html_tag:
			parser = MyHTMLParser()
			parser.feed(text_body)
			text_body = parser.get_text()
			weibo["text"] = text_body
		
		else:
			weibo["text"] = text_body
		weibo["article_url"] = self.get_article_url(selector)
		weibo["pics"] = self.get_pics(weibo_info)
		weibo["video_url"] = self.get_video_url(weibo_info)
		weibo["location"] = self.get_location(selector)
		weibo["created_at"] = weibo_info["created_at"]
		weibo["source"] = weibo_info["source"]
		weibo["attitudes_count"] = WeiBoSpider.string_to_int(
			weibo_info.get("attitudes_count", 0)
		)
		weibo["comments_count"] = WeiBoSpider.string_to_int(
			weibo_info.get("comments_count", 0)
		)
		weibo["reposts_count"] = WeiBoSpider.string_to_int(weibo_info.get("reposts_count", 0))
		weibo["topics"] = self.get_topics(selector)
		weibo["at_users"] = self.get_at_users(selector)
		return self.standardize_info(weibo)
	
	def get_at_users(self, selector):
		"""获取@用户"""
		a_list = selector.xpath("//a")
		at_users = ""
		at_list = []
		for a in a_list:
			if "@" + a.xpath("@href")[0][3:] == a.xpath("string(.)"):
				at_list.append(a.xpath("string(.)")[1:])
		if at_list:
			at_users = ",".join(at_list)
		return at_users
	
	def get_topics(self, selector):
		"""获取参与的微博话题"""
		span_list = selector.xpath("//span[@class='surl-text']")
		topics = ""
		topic_list = []
		for span in span_list:
			text = span.xpath("string(.)")
			if len(text) > 2 and text[0] == "#" and text[-1] == "#":
				topic_list.append(text[1:-1])
		if topic_list:
			topics = ",".join(topic_list)
		return topics
	
	def get_long_weibo(self, id):
		"""获取长微博"""
		for i in range(5):
			url = "https://m.weibo.cn/detail/%s" % id
			logger.info(f"""URL: {url} """)
			html = requests.get(url, headers=self.headers, verify=False).text
			html = html[html.find('"status":'):]
			html = html[: html.rfind('"call"')]
			html = html[: html.rfind(",")]
			html = "{" + html + "}"
			js = json.loads(html, strict=False)
			weibo_info = js.get("status")
			if weibo_info:
				weibo = self.parse_weibo(weibo_info)
				return weibo
			sleep(random.randint(6, 10))
	
	def get_one_weibo(self, info):
		"""获取一条微博的全部信息"""
		try:
			weibo_info = info["mblog"]
			weibo_id = weibo_info["id"]
			retweeted_status = weibo_info.get("retweeted_status")
			is_long = (
				True if weibo_info.get("pic_num") > 9 else weibo_info.get("isLongText")
			)
			if retweeted_status and retweeted_status.get("id"):  # 转发
				retweet_id = retweeted_status.get("id")
				is_long_retweet = retweeted_status.get("isLongText")
				if is_long:
					weibo = self.get_long_weibo(weibo_id)
					if not weibo:
						weibo = self.parse_weibo(weibo_info)
				else:
					weibo = self.parse_weibo(weibo_info)
				if is_long_retweet:
					retweet = self.get_long_weibo(retweet_id)
					if not retweet:
						retweet = self.parse_weibo(retweeted_status)
				else:
					retweet = self.parse_weibo(retweeted_status)
				(
					retweet["created_at"],
					retweet["full_created_at"],
				) = self.standardize_date(retweeted_status["created_at"])
				weibo["retweet"] = retweet
			else:  # 原创
				if is_long:
					weibo = self.get_long_weibo(weibo_id)
					if not weibo:
						weibo = self.parse_weibo(weibo_info)
				else:
					weibo = self.parse_weibo(weibo_info)
			weibo["created_at"], weibo["full_created_at"] = self.standardize_date(
				weibo_info["created_at"]
			)
			return weibo
		except Exception as e:
			logger.exception(e)
	
	def standardize_date(self, created_at):
		"""标准化微博发布时间"""
		if "刚刚" in created_at:
			ts = datetime.now()
		elif "分钟" in created_at:
			minute = created_at[: created_at.find("分钟")]
			minute = timedelta(minutes=int(minute))
			ts = datetime.now() - minute
		elif "小时" in created_at:
			hour = created_at[: created_at.find("小时")]
			hour = timedelta(hours=int(hour))
			ts = datetime.now() - hour
		elif "昨天" in created_at:
			day = timedelta(days=1)
			ts = datetime.now() - day
		else:
			created_at = created_at.replace("+0800 ", "")
			ts = datetime.strptime(created_at, "%c")
		
		created_at = ts.strftime(DATEFORMAT)
		full_created_at = ts.strftime("%Y-%m-%d %H:%M:%S")
		return created_at, full_created_at
	
	def is_pinned_weibo(self, info):
		"""判断微博是否为置顶微博"""
		weibo_info = info["mblog"]
		isTop = weibo_info.get("isTop")
		if isTop:
			return True
		else:
			return False
	
	def get_one_page(self, page):
		"""获取一页的全部微博"""
		try:
			js = self.get_weibo_json(page)
			import json
			with open('js.json', 'w') as f:
				# 写入方式1，等价于下面这行
				json.dump(js, f)  # 把列表numbers内容写入到"list.json"文件中
			if js["ok"]:
				weibos = js["data"]["cards"]
				
				if self.query:
					weibos = weibos[0]["card_group"]
				for w in weibos:
					if w["card_type"] == 11:
						temp = w.get("card_group", [0])
						if len(temp) >= 1:
							w = temp[0] or w
						else:
							w = w
					if w["card_type"] == 9:
						wb = self.get_one_weibo(w)
						if wb:
							if wb["id"] in self.weibo_id_list:
								continue
							created_at = datetime.strptime(wb["created_at"], DATEFORMAT)
							since_date = datetime.strptime(
								self.user_config["since_date"], DATEFORMAT
							)
							if created_at < since_date:
								if self.is_pinned_weibo(w):
									continue
								else:
									logger.info(
										"{}已获取{}({})的第{}页{}微博{}".format(
											"-" * 30,
											self.user["screen_name"],
											self.user["id"],
											page,
											'包含"' + self.query + '"的'
											if self.query
											else "",
											"-" * 30,
										)
									)
									return True
							if (not self.only_crawl_original) or ("retweet" not in wb.keys()):
								self.weibo.append(wb)
								self.weibo_id_list.append(wb["id"])
								self.got_count += 1
								# 这里是系统日志输出，尽量别太杂
								logger.info(
									"已获取用户 {} 的微博，内容为 {}".format(
										self.user["screen_name"], wb["text"]
									)
								)
							# self.print_weibo(wb)
							else:
								logger.info("正在过滤转发微博")
			else:
				return True
			logger.info(
				"{}已获取{}({})的第{}页微博{}".format(
					"-" * 30, self.user["screen_name"], self.user["id"], page, "-" * 30
				)
			)
		except Exception as e:
			logger.exception(e)
	
	def get_pages(self):
		"""获取全部微博"""
		try:
			# 用户id不可用
			if self.get_user_info() != 0:
				return
			logger.info("准备搜集 {} 的微博".format(self.user["screen_name"]))
			since_date = datetime.strptime(self.user_config["since_date"], DATEFORMAT)
			today = datetime.today()
			if since_date <= today:  # since_date 若为未来则无需执行
				page_count = self.get_page_count()
				wrote_count = 0
				page1 = 0
				random_pages = random.randint(1, 5)
				self.start_date = datetime.now().strftime(DATEFORMAT)
				pages = range(self.start_page, page_count + 1)
				for page in tqdm(pages, desc="Progress"):
					is_end = self.get_one_page(page)
					if is_end:
						break
					
					if page % 20 == 0:  # 每爬20页写入一次文件
						self.write_data(wrote_count)
						wrote_count = self.got_count
					
					# 通过加入随机等待避免被限制。爬虫速度过快容易被系统限制(一段时间后限
					# 制会自动解除)，加入随机等待模拟人的操作，可降低被系统限制的风险。默
					# 认是每爬取1到5页随机等待6到10秒，如果仍然被限，可适当增加sleep时间
					if (page - page1) % random_pages == 0 and page < page_count:
						sleep(random.randint(6, 10))
						page1 = page
						random_pages = random.randint(1, 5)
				
				self.write_data(wrote_count)  # 将剩余不足20页的微博写入文件
			logger.info("微博爬取完成，共爬取%d条微博", self.got_count)
		except Exception as e:
			logger.exception(e)
	
	def write_json(self, wrote_count):
		"""将爬到的信息写入json文件"""
		data = {}
		path = self.get_filepath("json")
		if os.path.isfile(path):
			with codecs.open(path, "r", encoding="utf-8") as f:
				data = json.load(f)
		weibo_info = self.weibo[wrote_count:]
		data = self.update_json_data(data, weibo_info)
		with codecs.open(path, "w", encoding="utf-8") as f:
			json.dump(data, f, ensure_ascii=False)
		logger.info("%d条微博写入json文件完毕,保存路径:", self.got_count)
		logger.info(path)
	
	def update_json_data(self, data, weibo_info):
		"""更新要写入json结果文件中的数据，已经存在于json中的信息更新为最新值，不存在的信息添加到data中"""
		data["user"] = self.user
		if data.get("weibo"):
			is_new = 1  # 待写入微博是否全部为新微博，即待写入微博与json中的数据不重复
			for old in data["weibo"]:
				if weibo_info[-1]["id"] == old["id"]:
					is_new = 0
					break
			if is_new == 0:
				for new in weibo_info:
					flag = 1
					for i, old in enumerate(data["weibo"]):
						if new["id"] == old["id"]:
							data["weibo"][i] = new
							flag = 0
							break
					if flag:
						data["weibo"].append(new)
			else:
				data["weibo"] += weibo_info
		else:
			data["weibo"] = weibo_info
		return data
	
	def get_filepath(self, write_type):
		"""获取结果文件路径"""
		try:
			dir_name = self.user["screen_name"]
			if self.user_id_as_folder_name:
				dir_name = str(self.user_config["user_id"])
			file_dir = (
				os.path.split(os.path.realpath(__file__))[0]
				+ os.sep
				+ "weibo"
				+ os.sep
				+ dir_name
			)
			if write_type == "img" or write_type == "video":
				file_dir = file_dir + os.sep + write_type
			if not os.path.isdir(file_dir):
				os.makedirs(file_dir)
			if write_type == "img" or write_type == "video":
				return file_dir
			file_path = file_dir + os.sep + str(self.user_config["user_id"]) + "." + write_type
			return file_path
		except Exception as e:
			logger.exception(e)
	
	def get_write_info(self, wrote_count):
		"""获取要写入的微博信息"""
		write_info = []
		for w in self.weibo[wrote_count:]:
			wb = dict()
			for k, v in w.items():
				if k not in ["user_id", "screen_name", "retweet"]:
					if "unicode" in str(type(v)):
						v = v.encode("utf-8")
					if k == "id":
						v = str(v) + "\t"
					wb[k] = v
			if not self.only_crawl_original:
				if w.get("retweet"):
					wb["is_original"] = False
					for k2, v2 in w["retweet"].items():
						if "unicode" in str(type(v2)):
							v2 = v2.encode("utf-8")
						if k2 == "id":
							v2 = str(v2) + "\t"
						wb["retweet_" + k2] = v2
				else:
					wb["is_original"] = True
			write_info.append(wb)
		return write_info
	
	def get_result_headers(self):
		"""获取要写入结果文件的表头"""
		result_headers = [
			"id",
			"bid",
			"正文",
			"头条文章url",
			"原始图片url",
			"视频url",
			"位置",
			"日期",
			"工具",
			"点赞数",
			"评论数",
			"转发数",
			"话题",
			"@用户",
			"完整日期",
		]
		if not self.only_crawl_original:
			result_headers2 = ["是否原创", "源用户id", "源用户昵称"]
			result_headers3 = ["源微博" + r for r in result_headers]
			result_headers = result_headers + result_headers2 + result_headers3
		return result_headers
	
	def write_csv(self, wrote_count):
		"""将爬到的信息写入csv文件"""
		write_info = self.get_write_info(wrote_count)
		result_headers = self.get_result_headers()
		result_data = [w.values() for w in write_info]
		file_path = self.get_filepath("csv")
		self.csv_helper(result_headers, result_data, file_path)
	
	def csv_helper(self, headers, result_data, file_path):
		"""将指定信息写入csv文件"""
		if not os.path.isfile(file_path):
			is_first_write = 1
		else:
			is_first_write = 0
		
		with open(file_path, "a", encoding="utf-8-sig", newline="") as f:
			writer = csv.writer(f)
			if is_first_write:
				writer.writerows([headers])
			writer.writerows(result_data)
		if headers[0] == "id":
			logger.info("%d条微博写入csv文件完毕,保存路径:", self.got_count)
		else:
			logger.info("%s 信息写入csv文件完毕，保存路径:", self.user["screen_name"])
		logger.info(file_path)
	
	def download_files(self, file_type, weibo_type, wrote_count):
		"""下载文件(图片/视频)"""
		try:
			describe = ""
			if file_type == "img":
				describe = "图片"
				key = "pics"
			else:
				describe = "视频"
				key = "video_url"
			if weibo_type == "original":
				describe = "原创微博" + describe
			else:
				describe = "转发微博" + describe
			logger.info("即将进行%s下载", describe)
			file_dir = self.get_filepath(file_type)
			file_dir = file_dir + os.sep + describe
			if not os.path.isdir(file_dir):
				os.makedirs(file_dir)
			for w in tqdm(self.weibo[wrote_count:], desc="Download progress"):
				if weibo_type == "retweet":
					if w.get("retweet"):
						w = w["retweet"]
					else:
						continue
				if w.get(key):
					self.handle_download(file_type, file_dir, w.get(key), w)
			logger.info("%s下载完毕,保存路径:", describe)
			logger.info(file_dir)
		except Exception as e:
			logger.exception(e)
	
	def download_one_file(self, url, file_path, file_type, weibo_id):
		"""下载单个文件(图片/视频)"""
		try:
			
			file_exist = os.path.isfile(file_path)
			need_download = (not file_exist)
			if not need_download:
				return
			
			s = requests.Session()
			s.mount(url, HTTPAdapter(max_retries=5))
			try_count = 0
			success = False
			MAX_TRY_COUNT = 3
			while try_count < MAX_TRY_COUNT:
				downloaded = s.get(
					url, headers=self.headers, timeout=(5, 10), verify=False
				)
				try_count += 1
				fail_flg_1 = url.endswith(("jpg", "jpeg")) and not downloaded.content.endswith(b"\xff\xd9")
				fail_flg_2 = url.endswith("png") and not downloaded.content.endswith(b"\xaeB`\x82")
				
				if (fail_flg_1 or fail_flg_2):
					logger.debug("[DEBUG] failed " + url + "  " + str(try_count))
				else:
					success = True
					logger.debug("[DEBUG] success " + url + "  " + str(try_count))
					break
			
			if success:
				# 需要分别判断是否需要下载
				if not file_exist:
					with open(file_path, "wb") as f:
						f.write(downloaded.content)
						logger.debug("[DEBUG] save " + file_path)
			else:
				logger.debug("[DEBUG] failed " + url + " TOTALLY")
		except Exception as e:
			error_file = self.get_filepath(file_type) + os.sep + "not_downloaded.txt"
			with open(error_file, "ab") as f:
				url = str(weibo_id) + ":" + file_path + ":" + url + "\n"
				f.write(url.encode(sys.stdout.encoding))
			logger.exception(e)
	
	def handle_download(self, file_type, file_dir, urls, w):
		"""处理下载相关操作"""
		file_prefix = w["created_at"][:11].replace("-", "") + "_" + str(w["id"])
		if file_type == "img":
			if "," in urls:
				url_list = urls.split(",")
				for i, url in enumerate(url_list):
					index = url.rfind(".")
					if len(url) - index >= 5:
						file_suffix = ".jpg"
					else:
						file_suffix = url[index:]
					file_name = file_prefix + "_" + str(i + 1) + file_suffix
					file_path = file_dir + os.sep + file_name
					self.download_one_file(url, file_path, file_type, w["id"])
			else:
				index = urls.rfind(".")
				if len(urls) - index > 5:
					file_suffix = ".jpg"
				else:
					file_suffix = urls[index:]
				file_name = file_prefix + file_suffix
				file_path = file_dir + os.sep + file_name
				self.download_one_file(urls, file_path, file_type, w["id"])
		else:
			file_suffix = ".mp4"
			if ";" in urls:
				url_list = urls.split(";")
				if url_list[0].endswith(".mov"):
					file_suffix = ".mov"
				for i, url in enumerate(url_list):
					file_name = file_prefix + "_" + str(i + 1) + file_suffix
					file_path = file_dir + os.sep + file_name
					self.download_one_file(url, file_path, file_type, w["id"])
			else:
				if urls.endswith(".mov"):
					file_suffix = ".mov"
				file_name = file_prefix + file_suffix
				file_path = file_dir + os.sep + file_name
				self.download_one_file(urls, file_path, file_type, w["id"])
	
	def write_data(self, wrote_count):
		"""将爬到的信息写入文件或数据库"""
		if self.got_count > wrote_count:
			if "csv" in self.write_mode:
				self.write_csv(wrote_count)
			if "json" in self.write_mode:
				self.write_json(wrote_count)
			if self.original_pic_download:
				self.download_files("img", "original", wrote_count)
			if self.original_video_download:
				self.download_files("video", "original", wrote_count)
	
	# if "post" in self.write_mode:
	# 	self.write_post(wrote_count)
	# if "mysql" in self.write_mode:
	# 	self.weibo_to_mysql(wrote_count)
	# if "mongo" in self.write_mode:
	# 	self.weibo_to_mongodb(wrote_count)
	# if "sqlite" in self.write_mode:
	# 	self.weibo_to_sqlite(wrote_count)
	# if self.original_pic_download:
	# 	self.download_files("img", "original", wrote_count)
	# if self.original_video_download:
	# 	self.download_files("video", "original", wrote_count)
	
	def update_user_config_file(self, user_config_file_path):
		"""更新用户配置文件"""
		with open(user_config_file_path, "rb") as f:
			try:
				lines = f.read().splitlines()
				lines = [line.decode("utf-8-sig") for line in lines]
			except UnicodeDecodeError:
				logger.error("%s文件应为utf-8编码，请先将文件编码转为utf-8再运行程序", user_config_file_path)
				sys.exit()
			for i, line in enumerate(lines):
				info = line.split(" ")
				if len(info) > 0 and info[0].isdigit():
					if self.user_config["user_id"] == info[0]:
						if len(info) == 1:
							info.append(self.user["screen_name"])
							info.append(self.start_date)
						if len(info) == 2:
							info.append(self.start_date)
						if len(info) > 2:
							info[2] = self.start_date
						lines[i] = " ".join(info)
						break
		with codecs.open(user_config_file_path, "w", encoding="utf-8") as f:
			f.write("\n".join(lines))
	
	def start(self):
		"""运行爬虫"""
		try:
			for user_config in self.user_config_list:
				self.initialize_info(user_config)
				self.get_pages()
				logger.info("信息抓取完毕")
				logger.info("*" * 100)
				if self.user_config_file_path and self.user:
					self.update_user_config_file(self.user_config_file_path)
		except Exception as e:
			logger.exception(e)


def run():
	try:
		config_path = os.path.split(os.path.realpath(__file__))[0] + os.sep + "config.json"
		with open(config_path, encoding="utf-8") as f:
			config = json.loads(f.read())
		
		weibo_spider = WeiBoSpider(config)
		weibo_spider.start()  # 爬取微博信息
	
	except Exception as e:
		
		logger.exception(e)


if __name__ == '__main__':
	run()
