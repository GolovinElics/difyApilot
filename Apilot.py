import plugins
import requests
import re
import json
from urllib.parse import urlparse
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel import channel
from common.log import logger
from plugins import *
from datetime import datetime, timedelta
BASE_URL_VVHAN = "https://api.vvhan.com/api/"
BASE_URL_ALAPI = "https://v3.alapi.cn/api/"


@plugins.register(
    name="Apilot",
    desire_priority=88,
    hidden=False,
    desc="A plugin to handle specific keywords",
    version="0.2",
    author="vision",
)
class Apilot(Plugin):
    def __init__(self):
        super().__init__()
        try:
            self.conf = super().load_config()
            self.condition_2_and_3_cities = None  # 天气查询，存储重复城市信息，Initially set to None
            if not self.conf:
                logger.warn("[Apilot] inited but alapi_token not found in config")
                self.alapi_token = None # Setting a default value for alapi_token
                self.morning_news_text_enabled = False
            else:
                logger.info("[Apilot] inited and alapi_token loaded successfully")
                self.alapi_token = self.conf["alapi_token"]
                try:
                    self.morning_news_text_enabled = self.conf["morning_news_text_enabled"]
                except:
                    self.morning_news_text_enabled = False
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        except Exception as e:
            raise self.handle_error(e, "[Apiot] init failed, ignore ")

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT
        ]:
            return
        content = e_context["context"].content.strip()
        logger.debug("[Apilot] on_handle_context. content: %s" % content)

        if content == "早报":
            news = self.get_morning_news(self.alapi_token, self.morning_news_text_enabled)
            reply_type = ReplyType.IMAGE_URL if self.is_valid_url(news) else ReplyType.TEXT
            reply = self.create_reply(reply_type, news)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
            return
        if content == "摸鱼":
            moyu = self.get_moyu_calendar()
            reply_type = ReplyType.IMAGE_URL if self.is_valid_url(moyu) else ReplyType.TEXT
            reply = self.create_reply(reply_type, moyu)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
            return

        if content == "摸鱼视频":
            moyu = self.get_moyu_calendar_video()
            reply_type = ReplyType.VIDEO_URL if self.is_valid_url(moyu) else ReplyType.TEXT
            reply = self.create_reply(reply_type, moyu)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
            return

        if content == "八卦":
            bagua = self.get_mx_bagua()
            reply_type = ReplyType.IMAGE_URL if self.is_valid_url(bagua) else ReplyType.TEXT
            reply = self.create_reply(reply_type, bagua)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
            return

        if content.startswith("快递"):
            # Extract the part after "快递"
            tracking_number = content[2:].strip()

            tracking_number = tracking_number.replace('：', ':')  # 替换可能出现的中文符号
            # Check if alapi_token is available before calling the function
            if not self.alapi_token:
                self.handle_error("alapi_token not configured", "快递请求失败")
                reply = self.create_reply(ReplyType.TEXT, "请先配置alapi的token")
            else:
                # Check if the tracking_number starts with "SF" for Shunfeng (顺丰) Express
                if tracking_number.startswith("SF"):
                    # Check if the user has included the last four digits of the phone number
                    if ':' not in tracking_number:
                        reply = self.create_reply(ReplyType.TEXT, "顺丰快递需要补充寄/收件人手机号后四位，格式：SF12345:0000")
                        e_context["reply"] = reply
                        e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
                        return  # End the function here

                # Call query_express_info function with the extracted tracking_number and the alapi_token from config
                content = self.query_express_info(self.alapi_token, tracking_number)
                reply = self.create_reply(ReplyType.TEXT, content)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
            return

        horoscope_match = re.match(r'^([\u4e00-\u9fa5]{2}座)$', content)
        if horoscope_match:
            if content in ZODIAC_MAPPING:
                zodiac_english = ZODIAC_MAPPING[content]
                content = self.get_horoscope(self.alapi_token, zodiac_english)
                reply = self.create_reply(ReplyType.TEXT, content)
            else:
                reply = self.create_reply(ReplyType.TEXT, "请重新输入星座名称")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
            return

        hot_trend_match = re.search(r'(.{1,6})热榜$', content)
        if hot_trend_match:
            hot_trends_type = hot_trend_match.group(1).strip()  # 提取匹配的组并去掉可能的空格
            content = self.get_hot_trends(hot_trends_type)
            reply = self.create_reply(ReplyType.TEXT, content)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
            return


        # 天气查询
        weather_match = re.match(r'^(?:(.{2,7}?)(?:市|县|区|镇)?|(\d{7,9}))(:?今天|明天|后天|7天|七天)?(?:的)?天气$', content)
        if weather_match:
            # 如果匹配成功，提取第一个捕获组
            city_or_id = weather_match.group(1) or weather_match.group(2)
            date = weather_match.group(3)
            if not self.alapi_token:
                self.handle_error("alapi_token not configured", "天气请求失败")
                reply = self.create_reply(ReplyType.TEXT, "请先配置alapi的token")
            else:
                content = self.get_weather(self.alapi_token, city_or_id, date, content)
                reply = self.create_reply(ReplyType.TEXT, content)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
            return

    def get_help_text(self, verbose=False, **kwargs):
        short_help_text = " 发送特定指令以获取早报、热榜、查询天气、星座运势、快递信息等！"

        if not verbose:
            return short_help_text

        help_text = "📚 发送关键词获取特定信息！\n"

        # 娱乐和信息类
        help_text += "\n🎉 娱乐与资讯：\n"
        help_text += '  🌅 早报: 发送"早报"获取早报。\n'
        help_text += '  🐟 摸鱼: 发送"摸鱼"获取摸鱼人日历。\n'
        help_text += '  🔥 热榜: 发送"xx热榜"查看支持的热榜。\n'
        help_text += '  🔥 八卦: 发送"八卦"获取明星八卦。\n'

        # 查询类
        help_text += "\n🔍 查询工具：\n"
        help_text += '  🌦️ 天气: 发送"城市+天气"查天气，如"北京天气"。\n'
        help_text += '  📦 快递: 发送"快递+单号"查询快递状态。如"快递112345655"\n'
        help_text += '  🌌 星座: 发送星座名称查看今日运势，如"白羊座"。\n'

        return help_text

    def get_morning_news(self, alapi_token, morning_news_text_enabled):
        if not alapi_token:
            url = BASE_URL_VVHAN + "60s?type=json"
            payload = "format=json"
            headers = {'Content-Type': "application/x-www-form-urlencoded"}
            try:
                morning_news_info = self.make_request(url, method="POST", headers=headers, data=payload)
                if isinstance(morning_news_info, dict) and morning_news_info['success']:
                    if morning_news_text_enabled:
                        # 提取并格式化新闻
                        news_list = ["{}. {}".format(idx, news) for idx, news in enumerate(morning_news_info["data"][:-1], 1)]
                        formatted_news = f"☕ {morning_news_info['data']['date']}  今日早报\n"
                        formatted_news = formatted_news + "\n".join(news_list)
                        weiyu = morning_news_info["data"][-1].strip()
                        return f"{formatted_news}\n\n{weiyu}\n\n 图片url：{morning_news_info['imgUrl']}"
                    else:
                        return morning_news_info['imgUrl']
                else:
                    return self.handle_error(morning_news_info, '早报信息获取失败，可配置"alapi token"切换至 Alapi 服务，或者稍后再试')
            except Exception as e:
                return self.handle_error(e, "出错啦，稍后再试")
        else:
            url = BASE_URL_ALAPI + "zaobao"
            data = {
                "token": alapi_token,
                "format": "json"
            }
            headers = {'Content-Type': "application/x-www-form-urlencoded"}
            try:
                morning_news_info = self.make_request(url, method="POST", headers=headers, data=data)
                if isinstance(morning_news_info, dict) and morning_news_info.get('code') == 200:
                    img_url = morning_news_info['data']['image']
                    if morning_news_text_enabled:
                        news_list = morning_news_info['data']['news']
                        weiyu = morning_news_info['data']['weiyu']

                        # 整理新闻为有序列表
                        formatted_news = f"☕ {morning_news_info['data']['date']}  今日早报\n"
                        formatted_news = formatted_news + "\n".join(news_list)
                        # 组合新闻和微语
                        return f"{formatted_news}\n\n{weiyu}\n\n 图片url：{img_url}"
                    else:
                        return img_url
                else:
                    return self.handle_error(morning_news_info, "早报获取失败，请检查 token 是否有误")
            except Exception as e:
                return self.handle_error(e, "早报获取失败")

    def get_moyu_calendar(self):
        url = BASE_URL_VVHAN + "moyu?type=json"
        payload = "format=json"
        headers = {'Content-Type': "application/x-www-form-urlencoded"}
        moyu_calendar_info = self.make_request(url, method="POST", headers=headers, data=payload)
        # 验证请求是否成功
        if isinstance(moyu_calendar_info, dict) and moyu_calendar_info['success']:
            return moyu_calendar_info['url']
        else:
            url = "https://dayu.qqsuu.cn/moyuribao/apis.php?type=json"
            payload = "format=json"
            headers = {'Content-Type': "application/x-www-form-urlencoded"}
            moyu_calendar_info = self.make_request(url, method="POST", headers=headers, data=payload)
            if isinstance(moyu_calendar_info, dict) and moyu_calendar_info['code'] == 200:
                moyu_pic_url = moyu_calendar_info['data']
                if self.is_valid_image_url(moyu_pic_url):
                    return moyu_pic_url
                else:
                    return '周末无需摸鱼，愉快玩耍吧'
            else:
                return '暂无可用摸鱼服务，认真上班'

    def get_moyu_calendar_video(self):
        url = "https://dayu.qqsuu.cn/moyuribaoshipin/apis.php?type=json"
        payload = "format=json"
        headers = {'Content-Type': "application/x-www-form-urlencoded"}
        moyu_calendar_info = self.make_request(url, method="POST", headers=headers, data=payload)
        logger.debug(f"[Apilot] moyu calendar video response: {moyu_calendar_info}")
        # 验证请求是否成功
        if isinstance(moyu_calendar_info, dict) and moyu_calendar_info['code'] == 200:
            moyu_video_url = moyu_calendar_info['data']
            if self.is_valid_image_url(moyu_video_url):
                return moyu_video_url

        # 未成功请求到视频时，返回提示信息
        return "视频版没了，看看文字版吧"

    def get_horoscope(self, alapi_token, astro_sign: str, time_period: str = "today"):
        if not alapi_token:
            url = BASE_URL_VVHAN + "horoscope"
            params = {
                'type': astro_sign,
                'time': time_period
            }
            try:
                horoscope_data = self.make_request(url, "GET", params=params)
                if isinstance(horoscope_data, dict) and horoscope_data['success']:
                    data = horoscope_data['data']

                    result = (
                        f"{data['title']} ({data['time']}):\n\n"
                        f"💡【每日建议】\n宜：{data['todo']['yi']}\n忌：{data['todo']['ji']}\n\n"
                        f"📊【运势指数】\n"
                        f"总运势：{data['index']['all']}\n"
                        f"爱情：{data['index']['love']}\n"
                        f"工作：{data['index']['work']}\n"
                        f"财运：{data['index']['money']}\n"
                        f"健康：{data['index']['health']}\n\n"
                        f"🍀【幸运提示】\n数字：{data['luckynumber']}\n"
                        f"颜色：{data['luckycolor']}\n"
                        f"星座：{data['luckyconstellation']}\n\n"
                        f"✍【简评】\n{data['shortcomment']}\n\n"
                        f"📜【详细运势】\n"
                        f"总运：{data['fortunetext']['all']}\n"
                        f"爱情：{data['fortunetext']['love']}\n"
                        f"工作：{data['fortunetext']['work']}\n"
                        f"财运：{data['fortunetext']['money']}\n"
                        f"健康：{data['fortunetext']['health']}\n"
                    )

                    return result

                else:
                    return self.handle_error(horoscope_data, '星座信息获取失败，可配置"alapi token"切换至 Alapi 服务，或者稍后再试')

            except Exception as e:
                return self.handle_error(e, "出错啦，稍后再试")
        else:
            # 使用 ALAPI 的 URL 和提供的 token
            url = BASE_URL_ALAPI + "star"
            payload = f"token={alapi_token}&star={astro_sign}"
            headers = {'Content-Type': "application/x-www-form-urlencoded"}
            try:
                horoscope_data = self.make_request(url, method="POST", headers=headers, data=payload)
                if isinstance(horoscope_data, dict) and horoscope_data.get('code') == 200:
                    data = horoscope_data['data']['day']

                    # 格式化并返回 ALAPI 提供的星座信息
                    result = (
                        f"📅 日期：{data['date']}\n\n"
                        f"💡【每日建议】\n宜：{data['yi']}\n忌：{data['ji']}\n\n"
                        f"📊【运势指数】\n"
                        f"总运势：{data['all']}\n"
                        f"爱情：{data['love']}\n"
                        f"工作：{data['work']}\n"
                        f"财运：{data['money']}\n"
                        f"健康：{data['health']}\n\n"
                        f"🔔【提醒】：{data['notice']}\n\n"
                        f"🍀【幸运提示】\n数字：{data['lucky_number']}\n"
                        f"颜色：{data['lucky_color']}\n"
                        f"星座：{data['lucky_star']}\n\n"
                        f"✍【简评】\n总运：{data['all_text']}\n"
                        f"爱情：{data['love_text']}\n"
                        f"工作：{data['work_text']}\n"
                        f"财运：{data['money_text']}\n"
                        f"健康：{data['health_text']}\n"
                    )
                    return result
                else:
                    return self.handle_error(horoscope_data, "星座获取信息获取失败，请检查 token 是否有误")
            except Exception as e:
                return self.handle_error(e, "出错啦，稍后再试")

    def get_hot_trends(self, hot_trends_type):
        # 查找映射字典以获取API参数
        hot_trends_type_en = hot_trend_types.get(hot_trends_type, None)
        if hot_trends_type_en is not None:
            url = BASE_URL_VVHAN + "hotlist/" + hot_trends_type_en
            try:
                data = self.make_request(url, "GET", {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                })
                if isinstance(data, dict) and data['success'] == True:
                    output = []
                    topics = data['data']
                    output.append(f'更新时间：{data["update_time"]}\n')
                    for i, topic in enumerate(topics[:15], 1):
                        hot = topic.get('hot', '无热度参数, 0')
                        formatted_str = f"{i}. {topic['title']} ({hot} 浏览)\nURL: {topic['url']}\n"
                        output.append(formatted_str)
                    return "\n".join(output)
                else:
                    return self.handle_error(data, "热榜获取失败，请稍后再试")
            except Exception as e:
                return self.handle_error(e, "出错啦，稍后再试")
        else:
            supported_types = "/".join(hot_trend_types.keys())
            final_output = (
                f"👉 已支持的类型有：\n\n    {supported_types}\n"
                f"\n📝 请按照以下格式发送：\n    类型+热榜  例如：微博热榜"
            )
            return final_output

    def query_express_info(self, alapi_token, tracking_number, com="", order="asc"):
        url = BASE_URL_ALAPI + "kd"
        payload = f"token={alapi_token}&number={tracking_number}&com={com}&order={order}"
        headers = {'Content-Type': "application/x-www-form-urlencoded"}

        try:
            response_json = self.make_request(url, method="POST", headers=headers, data=payload)

            if not isinstance(response_json, dict) or response_json is None:
                return f"查询失败：api响应为空"
            code = response_json.get("code", None)
            if code != 200:
                msg = response_json.get("msg", "未知错误")
                self.handle_error(msg, f"错误码{code}")
                return f"查询失败，{msg}"
            data = response_json.get("data", None)
            formatted_result = [
                f"快递编号：{data.get('nu')}",
                f"快递公司：{data.get('com')}",
                f"状态：{data.get('status_desc')}",
                "状态信息："
            ]
            for info in data.get("info"):
                time_str = info.get('time')[5:-3]
                formatted_result.append(f"{time_str} - {info.get('status_desc')}\n    {info.get('content')}")

            return "\n".join(formatted_result)

        except Exception as e:
            return self.handle_error(e, "快递查询失败")

    def get_weather(self, alapi_token, city_or_id: str, date: str, content):
        url = BASE_URL_ALAPI + 'tianqi'
        isFuture = date in ['明天', '后天', '七天', '7天']
        if isFuture:
            url = BASE_URL_ALAPI + 'tianqi/seven'
        
        # 判断使用id还是city请求api
        if city_or_id.isnumeric():  # 判断是否为纯数字，也即是否为 city_id
            params = {
                'city_id': city_or_id,
                'token': alapi_token
            }
        else:
            city_info = self.check_multiple_city_ids(city_or_id)
            if city_info:
                data = city_info['data']
                formatted_city_info = "\n".join(
                    [f"{idx + 1}) {entry['province']}--{entry['leader']}, ID: {entry['city_id']}"
                     for idx, entry in enumerate(data)]
                )
                return f'查询 <{city_or_id}> 具有多条数据：\n{formatted_city_info}\n请使用id查询，发送"id天气"'

            params = {
                'city': city_or_id,
                'token': alapi_token
            }
        
        try:
            weather_data = self.make_request(url, "GET", params=params)
            if isinstance(weather_data, dict) and weather_data.get('code') == 200:
                data = weather_data['data']
                
                # 处理七天天气预报
                if isFuture and isinstance(data, list):
                    formatted_output = []
                    for num, d in enumerate(data):
                        if num == 0:
                            formatted_output.append(f"城市: {d['city']} ({d['province']})\n")
                        if date == '明天' and num != 1:
                            continue
                        if date == '后天' and num != 2:
                            continue
                        basic_info = [
                            f"日期: {d['date']}",
                            f"天气: {d['wea_day']} | {d['wea_night']}",
                            f"温度: {d['temp_day']}℃ | {d['temp_night']}℃",
                            f"日出/日落: {d['sunrise']} / {d['sunset']}"
                        ]
                        if 'index' in d:
                            for i in d['index']:
                                basic_info.append(f"{i['name']}: {i['level']}")
                        formatted_output.append("\n".join(basic_info) + '\n')
                    return "\n".join(formatted_output)
                
                # 处理当天天气
                if not city_or_id.isnumeric() and data['city'] not in content:
                    return '输入不规范，请输<国内城市+(今天|明天|后天|七天|7天)+天气>，比如 "广州天气"'
                
                formatted_output = []
                
                # 基本天气信息
                update_time = datetime.strptime(data['update_time'], "%Y-%m-%d %H:%M:%S").strftime("%m-%d %H:%M")
                basic_info = (
                    f"城市: {data['city']} ({data['province']})\n"
                    f"更新: {update_time}\n"
                    f"天气: {data['weather']}\n"
                    f"温度: ↓{data['min_temp']}℃ | 现{data['temp']}℃ | ↑{data['max_temp']}℃\n"
                    f"风向: {data['wind']}\n"
                    f"湿度: {data['humidity']}\n"
                    f"日出/日落: {data['sunrise']} / {data['sunset']}\n"
                )
                formatted_output.append(basic_info)
                
                # 穿衣指数
                if 'index' in data and 'chuangyi' in data['index']:
                    chuangyi = data['index']['chuangyi']
                    formatted_output.append(f"穿衣指数: {chuangyi['level']} - {chuangyi['content']}\n")
                
                # 未来天气预报
                if 'hour' in data:
                    dt_object = datetime.strptime(data['update_time'], "%Y-%m-%d %H:%M:%S")
                    ten_hours_later = dt_object + timedelta(hours=10)
                    future_weather = []
                    
                    for hour_info in data['hour']:
                        forecast_time = datetime.strptime(hour_info['time'], "%Y-%m-%d %H:%M:%S")
                        if dt_object < forecast_time <= ten_hours_later:
                            future_weather.append(
                                f"     {forecast_time.hour:02d}:00 - {hour_info['wea']} - {hour_info['temp']}°C"
                            )
                    
                    if future_weather:
                        formatted_output.append("未来10小时的天气预报:\n" + "\n".join(future_weather))
                
                # 预警信息
                if 'alarm' in data and data['alarm']:
                    alarm_info = "预警信息:\n"
                    for alarm in data['alarm']:
                        alarm_info += (
                            f"标题: {alarm['title']}\n"
                            f"等级: {alarm['level']}\n"
                            f"类型: {alarm['type']}\n"
                            f"提示: \n{alarm['tips']}\n"
                            f"内容: \n{alarm['content']}\n\n"
                        )
                    formatted_output.append(alarm_info)
                
                return "\n".join(formatted_output)
            else:
                return self.handle_error(weather_data, "获取失败，请查看服务器log")
            
        except Exception as e:
            return self.handle_error(e, "获取天气信息失败")

    def get_mx_bagua(self):
        url = "https://dayu.qqsuu.cn/mingxingbagua/apis.php?type=json"
        payload = "format=json"
        headers = {'Content-Type': "application/x-www-form-urlencoded"}
        bagua_info = self.make_request(url, method="POST", headers=headers, data=payload)
        # 验证请求是否成功
        if isinstance(bagua_info, dict) and bagua_info['code'] == 200:
            bagua_pic_url = bagua_info["data"]
            if self.is_valid_image_url(bagua_pic_url):
                return bagua_pic_url
            else:
                return "周末不更新，请微博吃瓜"
        else:
            logger.error(f"错误信息：{bagua_info}")
            return "暂无明星八卦，吃瓜莫急"

    def make_request(self, url, method="GET", headers=None, params=None, data=None, json_data=None):
        try:
            # 设置默认超时时间
            timeout = 10
            # 设置默认请求头
            if headers is None:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
            
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
            elif method.upper() == "POST":
                response = requests.post(url, headers=headers, data=data, json=json_data, timeout=timeout)
            else:
                return {"success": False, "message": "Unsupported HTTP method"}

            # 检查响应状态码
            response.raise_for_status()
            
            # 尝试解析JSON响应
            try:
                return response.json()
            except ValueError:
                # 如果响应不是JSON格式，返回原始文本
                return {"success": False, "message": f"Invalid JSON response: {response.text[:100]}..."}
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {str(e)}")
            return {"success": False, "message": f"Request failed: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return {"success": False, "message": f"Unexpected error: {str(e)}"}


    def create_reply(self, reply_type, content):
        reply = Reply()
        reply.type = reply_type
        reply.content = content
        return reply

    def handle_error(self, error, message):
        logger.error(f"{message}，错误信息：{error}")
        return message

    def is_valid_url(self, url):
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except ValueError:
            return False

    def is_valid_image_url(self, url):
        try:
            response = requests.head(url)  # Using HEAD request to check the URL header
            # If the response status code is 200, the URL exists and is reachable.
            return response.status_code == 200
        except requests.RequestException as e:
            # If there's an exception such as a timeout, connection error, etc., the URL is not valid.
            return False

    def load_city_conditions(self):
        if self.condition_2_and_3_cities is None:
            try:
                json_file_path = os.path.join(os.path.dirname(__file__), 'duplicate-citys.json')
                with open(json_file_path, 'r', encoding='utf-8') as f:
                    self.condition_2_and_3_cities = json.load(f)
            except Exception as e:
                return self.handle_error(e, "加载condition_2_and_3_cities.json失败")


    def check_multiple_city_ids(self, city):
        self.load_city_conditions()
        city_info = self.condition_2_and_3_cities.get(city, None)
        if city_info:
            return city_info
        return None


ZODIAC_MAPPING = {
        '白羊座': 'aries',
        '金牛座': 'taurus',
        '双子座': 'gemini',
        '巨蟹座': 'cancer',
        '狮子座': 'leo',
        '处女座': 'virgo',
        '天秤座': 'libra',
        '天蝎座': 'scorpio',
        '射手座': 'sagittarius',
        '摩羯座': 'capricorn',
        '水瓶座': 'aquarius',
        '双鱼座': 'pisces'
    }

hot_trend_types = {
    "微博": "wbHot",
    "虎扑": "huPu",
    "知乎": "zhihuHot",
    "知乎日报": "zhihuDay",
    "哔哩哔哩": "bili",
    "36氪": "36Ke",
    "抖音": "douyinHot",
    "IT": "itNews",
    "虎嗅": "huXiu",
    "产品经理": "woShiPm",
    "头条": "toutiao",
    "百度": "baiduRD",
    "豆瓣": "douban",
}
