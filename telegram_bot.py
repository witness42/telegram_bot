"""
VERSION = 0.9
AUTHOR = "David Büchner"
AUTHOR_EMAIL = "david@it-buechner.de"
DESCRIPTION = "Telegram Bot for the OpenAI API"

TODO: web crawling, activate window method, document translation, if more users import asyncio and use locking anywhere
"""

import logging
import os
import sys
import time
import datetime
import telebot
import openai
import tiktoken
import configparser
import requests
import json

openai.api_key = os.environ.get("OPENAI_API_KEY")
deepl_api_key = os.environ.get("DEEPL_API_KEY")

if (len(sys.argv) != 3):
    print("Usage: python3.9 telegram_bot.py <main_folder_path>(e.g. /home/dummyuser/shitty_telegram_bot/) <config_name> (e.g. shitty_telegram_bot)")
    sys.exit(1)
MAIN_PATH = sys.argv[1]
CONFIG_NAME = sys.argv[2]

""" Read config file """
config = configparser.ConfigParser()
config.read(f"{MAIN_PATH}{CONFIG_NAME}.conf")

LOG_LEVELS = {None: logging.DEBUG, "debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
              "error": logging.ERROR, "critical": logging.CRITICAL}
LOG_LEVEL = LOG_LEVELS[config.get("log", "level", fallback=None)]
logging.basicConfig(filename=f"{MAIN_PATH}odin.log", level=LOG_LEVEL,
                            format="%(asctime)s [%(levelname)-8s] %(process)d %(module)s (%(lineno)d): %(message)s")

LOCK_DIR = config.get("lock", "dir")

PERSONA_NAME = config.get("persona", "name")
SYSTEM_MSG = config.get("persona", "system")
WELCOME_MSG = config.get("persona", "welcome")
FORGET_MSG = config.get("persona", "forget")
NUM_IMAGES = int(config.get("persona", "num_images")) # [1, 10]
NOT_FORGOTTEN_MSG = config.get("persona", "notforgotten")
ERROR_MSG = config.get("persona", "error")

MODEL = config.get("openai", "model")
TEMPERATURE = int(config.get("openai", "temperature"))
MAX_TOKENS = int(config.get("openai", "max_tokens"))
ENCODING = tiktoken.encoding_for_model(MODEL)

bot = telebot.TeleBot(config.get("telegram", "token"))

logging.info(f'{bot.user.username} is ready!')

user_context = {}
admins = set([int(x) for x in config.get("acl", "admins").split(",")])
allowed_users = set([int(x) for x in config.get("acl", "users").split(",")])
allowed_groups = set([int(x) for x in config.get("acl", "groups").split(",")])
already_restriced_users = set()

class Context:
    def __init__(self, user_id):
        self.user_id = user_id
        self.context = [{"role": "system", "content": SYSTEM_MSG}]

    def add_message(self, message):
        self.context.append(message)

    def get_context(self):
        return self.context

    def remove_message(self, message):
        self.context.remove(message)


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        bot.reply_to(message, WELCOME_MSG)


@bot.message_handler(commands=['forget'])
def clear_context(message):
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        if user_context.get(message.from_user.id, None) is None:
            bot.reply_to(message, NOT_FORGOTTEN_MSG)
            return
        del user_context[message.from_user.id]
        bot.reply_to(message, FORGET_MSG)
    else:
        log_unrestricted(message)

@bot.message_handler(commands=['adduser'])
def add_user(message):
    if not message.from_user.id in allowed_users:
        log_unrestricted(message)
        return
    if message.from_user.id in admins:
        if len(message.text.split()) == 2 and len(list(message.text.split()[1])) == 9:
            try:
                allowed_users.add(int(message.text.split()[1]))
                with open(f"{MAIN_PATH}{CONFIG_NAME}.conf", "a") as f:
                    for line in f.readlines():
                        if line.startswith("users:"):
                            for l in line.strip():
                                if l == f"{message.text.split()[1]},":
                                    bot.reply_to(message, "User is already allowed!")
                            nline = line.strip() + f", {message.text.split()[1]}"
                            f.write(nline)
                            break
                bot.reply_to(message, f"Added user {message.text.split()[1]}")
            except ValueError:
                bot.reply_to(message, "Please enter a valid user id!")
        else:
            bot.reply_to(message, "Please enter a user id!")
    else:
        bot.reply_to(message, "You are not allowed to use this command!")

@bot.message_handler(commands=['restart'])
def restart(message):
    if not message.from_user.id in allowed_users:
        log_unrestricted(message)
        return
    if message.from_user.id in admins:
        bot.reply_to(message, "Restarting...")
        os.system(f"systemctl restart {PERSONA_NAME}.service")
    else:
        bot.reply_to(message, "You are not allowed to use this command!")

@bot.message_handler(commands=['getuserid'])
def get_user_id(message):
    if message.from_user.id in allowed_users:
        bot.reply_to(message, f"{message.from_user.id}")
    else:
        log_unrestricted(message)


@bot.message_handler(commands=[PERSONA_NAME])
def send_message(message, transcript = None):
    if message.chat.type == "group" and transcript is None:
        return
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        start_time = time.time()
        if transcript is not None:
            message.text = transcript
            logging.info("Voice processed message:")
        logging.info(f"{message.from_user.first_name}({message.from_user.id}): {message.text}")
        if user_context.get(message.from_user.id, None) is None:
            user_context[message.from_user.id] = Context(message.from_user.id)
        context_obj = user_context[message.from_user.id]
        msg = {"role": "user", "content": message.text}
        context_obj.add_message(msg)
        # window method
        """ 
        content_buf = []
        for i in context_obj.get_context():
            content_buf.append(i["content"])
        num_token = len(encoding.encode(str(content_buf)))
        token_count = random.randint(100, num_token - 100)
        context = user_context[message.from_user.id].get_context()
        while token_count > 0:
            token_count -= 1
            ran_message = random.randint(1, len(context) - 1)  # exkludiere 0 die system message
            msg_len = len(context[ran_message]['content'])
            ran_token = random.randint(0, msg_len - 1)
            del context[ran_message]['content'][ran_token]
        """
        output = {"role": "assistant", "content": ""}
        try:
            response = openai.ChatCompletion.create(
                model=MODEL,
                api_key=openai.api_key,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                messages=user_context[message.from_user.id].get_context()
            )
            output["content"] = response['choices'][0]['message']['content']
            context_obj.add_message(output)
            context = user_context[message.from_user.id].get_context()
            num_token = len(ENCODING.encode(str(context)))
            while not lock():
                time.sleep(1)
            logging.info("token: " + str(num_token))
            logging.info(str(context))
            if num_token > 1500:
                context_obj.remove_message(context[1])
                context_obj.remove_message(context[2])
            stop_time = time.time()
            logging.info("time taken: " + str(round(start_time - stop_time, 2)) + " seconds")
            remove_lock()
            bot.reply_to(message, output['content'], parse_mode='Markdown')
        except telebot.apihelper.ApiTelegramException as e:
            logging.error(str(e))
            try:
                bot.reply_to(message, output['content'])
            except Exception as e:
                logging.error(f"second try due to {str(e)}")
                remove_lock()
                bot.reply_to(message, ERROR_MSG)
        except Exception as e:
            logging.error(str(e))
            remove_lock()
            bot.reply_to(message, ERROR_MSG)
    else:
        log_unrestricted(message)

@bot.message_handler(commands=['generate'])
def generate(message):
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        start_time = time.time()
        if message.text[10:] == "":
            bot.reply_to(message, "Please enter a prompt for the image generation")
            return
        bot.reply_to(message, "Your image is being drawn...")
        logging.info(f"{message.from_user.first_name}({message.from_user.id}): Image generation message({message.text[10:]})")
        try:
            response = openai.Image.create(
                prompt=message.text[10:],
                api_key=openai.api_key,
                n=NUM_IMAGES,
                size="1024x1024"
            )
            image_url = response['data'][0]['url']
            response = requests.get(image_url)
            stop_time = time.time()
            logging.info("time taken for image generation: " + str(round(start_time - stop_time, 2)) + " seconds")
            bot.send_photo(message.chat.id, response.content, caption=message.text[10:] + "\ntime taken for image generation: " + str(round(start_time - stop_time, 2)) + " seconds")
        except openai.error.OpenAIError as e:
            logging.error(f"HTTP STATUS: {e.http_status}, ERROR: {e.error}")
            bot.reply_to(message, str(e.error))
    else:
        log_unrestricted(message)

@bot.message_handler(content_types=['photo'])
def make_variation(message):
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        start_time = time.time()
        if message.caption not in ["make variation", "make variations", "m"]: # m is a shortcut
            return
        if message.caption == "make variations":
            more_images = True
            if NUM_IMAGES > 4:
                more_images = False
            bot.reply_to(message, "Generating variations...")
        else:
            if NUM_IMAGES > 1:
                bot.reply_to(message, "Generating variations...")
            else:
                bot.reply_to(message, "Generating variation...")
            more_images = False
        file_id = message.photo[-1].file_id
        file = bot.get_file(file_id)
        downloaded_file = bot.download_file(file.file_path)
        with open(f"{MAIN_PATH}image.png", 'wb') as new_file:
            new_file.write(downloaded_file)
        os.system(f"convert {MAIN_PATH}image.png -resize 1024x1024 {MAIN_PATH}image.png")
        try:
            response = openai.Image.create_variation(
                image=open(f"{MAIN_PATH}image.png", "rb"),
                n=4 if more_images else NUM_IMAGES,
                size="1024x1024"
            )
            image_url = response['data'][0]['url']
            response = requests.get(image_url)
            os.remove(f"{MAIN_PATH}image.png")
            stop_time = time.time()
            logging.info("time taken for image generation: " + str(round(start_time - stop_time, 2)) + " seconds")
            bot.send_photo(message.chat.id, response.content, caption="\ntime taken for image generation: " + str(round(start_time - stop_time, 2)) + " seconds")
        except openai.error.OpenAIError as e:
            logging.error(f"HTTP STATUS: {e.http_status}, ERROR: {e.error}")
            bot.reply_to(message, str(e.error))
    else:
        log_unrestricted(message)

@bot.message_handler(content_types=['voice'])
def voice_processing(message):
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        start_time = time.time()
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(f"{MAIN_PATH}new_file.ogg", 'wb') as audio_file:
            audio_file.write(downloaded_file)
        os.system(f"ffmpeg -i {MAIN_PATH}new_file.ogg -codec:a libmp3lame -qscale:a 2 {MAIN_PATH}new_file.mp3")
        with open(f"{MAIN_PATH}new_file.mp3", 'rb') as audio_file:
            transcript = openai.Audio.transcribe("whisper-1", audio_file)
            send_message(message, transcript["text"])
        os.system(f"mv {MAIN_PATH}new_file.mp3 {MAIN_PATH}recordings/{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp3")
        os.remove(f"{MAIN_PATH}new_file.ogg")
        stop_time = time.time()
        logging.info("time taken for voice processing: " + str(round(start_time - stop_time, 2)) + " seconds")
    else:
        log_unrestricted(message)

@bot.message_handler(content_types=['video'])
def translate_video(message):
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        start_time = time.time()
        if message.caption.lower() not in ["translate", "t", "translate to german", "tg"]: # t is a shortcut
            return
        bot.reply_to(message, "Translating video...")
        file_info = bot.get_file(message.video.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(f"{MAIN_PATH}video.mp4", 'wb') as video_file:
            video_file.write(downloaded_file)
        os.system(f"ffmpeg -i {MAIN_PATH}video.mp4 {MAIN_PATH}audio.mp3")
        os.remove(f"{MAIN_PATH}video.mp4")
        with open(f"{MAIN_PATH}audio.mp3", 'rb') as audio_file:
            transcript = openai.Audio.translate("whisper-1", audio_file)
        os.remove(f"{MAIN_PATH}audio.mp3")
        if message.caption.lower() in ["tg", "translate to german"]:
            url = 'https://api-free.deepl.com/v2/translate'
            payload = {'text': transcript["text"], 'target_lang': 'DE'}
            headers = {'Authorization': "DeepL-Auth-Key " + deepl_api_key,
                       'User-Agent': 'YourApp/1.2.3',
                       'Content-Type': 'application/x-www-form-urlencoded'}

            response = requests.post(url, data=payload, headers=headers)
            res = json.loads(response.text)
            logging.info(f"Translated video text for {message.from_user.first_name}({message.from_user.id}): {res['translations'][0]['text']}")
            bot.reply_to(message, res["translations"][0]["text"])
        else:
            logging.info(f"Translated video text for {message.from_user.first_name}({message.from_user.id}): {transcript['text']}")
            bot.reply_to(message, transcript["text"])
        stop_time = time.time()
        logging.info("time taken for video translation: " + str(round(start_time - stop_time, 2)) + " seconds")
    else:
        log_unrestricted(message)


def log_unrestricted(message):
    if message.from_user.id not in already_restriced_users:
        bot.reply_to(message, "You are not allowed to use me! You can ask https://t.me/earth_down for permission. Your user meta data and all messages are logged!")
        already_restriced_users.add(message.from_user.id)
    while not lock():
        time.sleep(1)
    logging.warning(str(message))
    remove_lock()

@bot.message_handler(func=lambda message: True)
def handle_default(message):
    send_message(message)

""" Create lock dir """
def lock():
    try:
        os.mkdir(LOCK_DIR)
    except FileExistsError:
        return False
    return True

""" Free lock dir """
def remove_lock():
    try:
        if os.path.exists(LOCK_DIR):
            os.rmdir(LOCK_DIR)
    except Exception as e:
        logging.critical(f"Cannot delete lock dir '{LOCK_DIR}': {e}")

bot.polling()
