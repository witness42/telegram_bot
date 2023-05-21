"""
VERSION = 0.9
AUTHOR = "David Büchner"
AUTHOR_EMAIL = "david@it-buechner.de"
DESCRIPTION = "Telegram Bot for the OpenAI API"
LICENSE = "GPLv3"

TODO: web crawling, activate window method, edit image
"""

import logging
import os
import time
import datetime
import telebot
import openai
import tiktoken
import subprocess
import configparser
import requests

openai.api_key = os.environ.get("OPENAI_API_KEY")

config_file = "example.conf" # for different personas just change this to a newly crafted config file

""" Read config file """
config = configparser.ConfigParser()
config.read(config_file)

LOG_FILE = config.get("log", "file")
LOG_LEVELS = {None: logging.DEBUG, "debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
              "error": logging.ERROR, "critical": logging.CRITICAL}
LOG_LEVEL = LOG_LEVELS[config.get("log", "level", fallback=None)]
logging.basicConfig(filename=LOG_FILE, level=LOG_LEVEL,
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


@bot.message_handler(commands=[PERSONA_NAME])
def send_message(message, transcript = None):
    if message.chat.type == "group" and transcript is None:
        return
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        start_time = time.time()
        if transcript is not None:
            message.text = transcript
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
            logging.info("time taken: " + str(round(start_time - stop_time, 2)) + "seconds")
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
        logging.info(f"{message.from_user.first_name}({message.from_user.id}): Image generation message({message.text})")
        try:
            response = openai.Image.create(
              prompt=message.text,
              api_key=openai.api_key,
              n=NUM_IMAGES,
              size="1024x1024"
            )
            image_url = response['data'][0]['url']
            response = requests.get(image_url)
            stop_time = time.time()
            logging.info("time taken for image generation: " + str(round(start_time - stop_time, 2)) + "seconds")
            bot.send_photo(message.chat.id, response.content, caption=message.text)
        except openai.error.OpenAIError as e:
          logging.error(f"HTTP STATUS: {e.http_status}, ERROR: {e.error}")
          bot.reply_to(message, e.error)
    else:
        log_unrestricted(message)

@bot.message_handler(content_types=['image'])
def edit_image(message):
    pass

@bot.message_handler(func=lambda message: True)
def on_reply(message):
    send_message(message)

@bot.message_handler(content_types=['voice'])
def voice_processing(message):
    if message.from_user.id in allowed_users or message.chat.id in allowed_groups:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open('new_file.ogg', 'wb') as audio_file:
            audio_file.write(downloaded_file)
        subprocess.call(["ffmpeg", "-i", "new_file.ogg", "-codec:a", "libmp3lame", "-qscale:a", "2", "new_file.mp3"])
        with open('new_file.mp3', 'rb') as audio_file:
            transcript = openai.Audio.transcribe("whisper-1", audio_file)
            #transcript = openai.Audio.translate("whisper-1", audio_file)
            send_message(message, transcript['text'])
        subprocess.call(["mv", "new_file.mp3", f"recordings/{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp3"])
        subprocess.call(["rm", "new_file.ogg"])
    else:
        log_unrestricted(message)

def log_unrestricted(message):
    if message.from_user.id not in already_restriced_users:
        bot.reply_to(message, "You are not allowed to use me! Your user meta data and all messages are logged!")
        already_restriced_users.add(message.from_user.id)
    while not lock():
        time.sleep(1)
    logging.warning(str(message))
    remove_lock()

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
