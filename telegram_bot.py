"""
VERSION = 0.9
AUTHOR = "David Büchner"
AUTHOR_EMAIL = "david@it-buechner.de"
DESCRIPTION = "Telegram Bot for the OpenAI API"

TODO: web crawling, activate window method, if more users import asyncio and use locking everywhere
"""

import configparser
import datetime
import json
import logging
import math
import os
import sys
import time
import uuid

# somehow pip install youtube_dl is broken, thus use pip install git+https://github.com/ytdl-org/youtube-dl.git@master#egg=youtube_dl
import youtube_dl as youtube_dl
from youtube_transcript_api import YouTubeTranscriptApi
import google.cloud.texttospeech as tts
import langid
import openai
import paypalrestsdk
import requests
import telebot
import tiktoken

# --- ENV VARS ---
openai.api_key = os.environ.get("OPENAI_API_KEY")
deepl_api_key = os.environ.get("DEEPL_API_KEY")

# --- COMMAND LINE ARGS ---
if len(sys.argv) != 3:
    print("Usage: python3.9 telegram_bot.py <main_folder_path>(e.g. /home/dummyuser/shitty_telegram_bot/) <config_name> (e.g. shitty_telegram_bot)")
    sys.exit(1)
MAIN_PATH = sys.argv[1]
CONFIG_NAME = sys.argv[2]

# --- CONFIG ---
config = configparser.ConfigParser()
config.read(f"{MAIN_PATH}{CONFIG_NAME}.conf")

LOG_LEVELS = {None: logging.DEBUG, "debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}
LOG_LEVEL = LOG_LEVELS[config.get("log", "level", fallback=None)]
logging.basicConfig(filename=f"{MAIN_PATH}{CONFIG_NAME}.log", level=LOG_LEVEL, format="%(asctime)s [%(levelname)-8s] %(process)d %(module)s (%(lineno)d): %(message)s")

DEBUG = config.getboolean("log", "debug")

LOCK_DIR = config.get("lock", "dir")

PERSONA_NAME = config.get("persona", "name")
SYSTEM_MSG = config.get("persona", "system")
WELCOME_MSG = config.get("persona", "welcome")
FORGET_MSG = config.get("persona", "forget")
NUM_IMAGES = int(config.get("persona", "num_images"))  # [1, 10]
NOT_FORGOTTEN_MSG = config.get("persona", "notforgotten")
ERROR_MSG = config.get("persona", "error")

MODEL = config.get("openai", "model")
TEMPERATURE = int(config.get("openai", "temperature"))
MAX_TOKENS = int(config.get("openai", "max_tokens"))
ENCODING = tiktoken.encoding_for_model(MODEL)

bot = telebot.TeleBot(config.get("telegram", "token"))

# --- ACL ---
user_context = {}
subscribed_users = set([int(x) for x in config.get("acl", "subscribed").split(",")])
admins = set([int(x) for x in config.get("acl", "admins").split(",")])
allowed_users = set([int(x) for x in config.get("acl", "users").split(",")])
allowed_users = allowed_users.union(subscribed_users)
already_restriced_users = set()

logging.info(f'{bot.user.username} is ready!')


# --- USER CONTEXT ---
class Context:
    def __init__(self, user_id):
        self.user_id = user_id
        self.context = [{"role": "system", "content": SYSTEM_MSG}]

    def add_message(self, message) -> None:
        self.context.append(message)

    def get_context(self) -> list:
        return self.context

    def remove_message(self, message) -> None:
        self.context.remove(message)


# --- SPLIT TEXT FOR TELEGRAM API ---
def message_to_list(text: str) -> list:
    message_chunks = math.ceil(len(text) / 4096)
    message_list = []
    for i in range(message_chunks):
        message_list.append(text[i * 4096:min((i + 1) * 4096, len(text))])
    return message_list


# --- PAYPAL ---
def create_payment_object() -> paypalrestsdk.Payment:
    paypalrestsdk.configure({
        "mode": "sandbox",
        "client_id": os.environ.get("PAYPAL_CLIENT_ID"),
        "client_secret": os.environ.get("PAYPAL_CLIENT_SECRET")
    })
    return paypalrestsdk.Payment(
        "create_payment_intent",
        {
            "intent": "sale",
            "payer": {
                "payment_method": "paypal",
            },
            "amount": {
                "total": 10,
                "currency": "EUR",
            },
            "description": f"Subscription Payment for the Telegram Bot: {bot.user.username}",
            # "redirect_urls": {
            #     "return_url": "http://www.yourdomain.com/paypal/success/?paymentID=PAY-1234567",
            #     "cancel_url": "http://www.yourdomain.com/paypal/fail/"
            # }
        }
    )


@bot.message_handler(commands=['subscribe'])
def subscribe(message: telebot.types.Message) -> None:
    if message.from_user.id in admins:
        if message.from_user.id in allowed_users:
            bot.reply_to(message, "You are already subscribed!")
            # return # TODO: remove this comment, after debugging
        payment = create_payment_object()

        if payment.create():
            for link in payment.links:
                bot.reply_to(message, link)
                if link.rel == 'approval_url':
                    bot.send_message(message.chat.id, f"Please approve payment: {link.href}")
            # handle_webhook(payment.id)
        else:
            bot.reply_to(message, "Something went wrong with the payment. Please try again later.")


# --- ADMIN COMMANDS ---
@bot.message_handler(commands=['log', 'docs', 'recordings', 'adduser', 'removeuser', 'stop', 'restart', 'reboot'])
def admin_command_entry(message: telebot.types.Message) -> None:
    if message.from_user.id not in allowed_users:
        log_unrestricted(message)
        return
    if message.from_user.id in admins:
        command = message.text.split(" ")[0].split("/")[1]
        if command == "log":
            send_log(message)
        elif command == "docs":
            send_docs(message)
        elif command == "recordings":
            send_recordings(message)
        elif command == "adduser":
            add_user(message)
        elif command == "removeuser":
            remove_user(message)
        elif command == "stop":
            stop(message)
        elif command == "restart":
            restart(message)
        elif command == "reboot":
            reboot(message)
        else:
            bot.reply_to(message, "Command not found!")
    else:
        send_message(message)


def send_log(message: telebot.types.Message) -> None:
    temp_uuid = str(uuid.uuid4())
    output_uuid = str(uuid.uuid4())
    os.system(f"cp {MAIN_PATH}{PERSONA_NAME}.log {MAIN_PATH}{temp_uuid}.log")
    if message.text[5:].isdigit():
        os.system(f"tail -n {message.text[5:]} {MAIN_PATH}{temp_uuid}.log > {MAIN_PATH}{output_uuid}.log")
        os.remove(f"{MAIN_PATH}{temp_uuid}.log")
        temp_uuid = output_uuid
    os.system(
        f"cat {MAIN_PATH}{temp_uuid}.log | iconv -f utf-8 -t iso-8859-1 -sc | enscript -X 88591 -o -| ps2pdf - {MAIN_PATH}{temp_uuid}.pdf")
    with open(f"{MAIN_PATH}{temp_uuid}.pdf", "rb") as f:
        bot.send_document(message.chat.id, f)
    f.close()
    os.remove(f"{MAIN_PATH}{temp_uuid}.pdf")
    os.remove(f"{MAIN_PATH}{output_uuid}.log")


@bot.message_handler(commands=['docs'])
def send_docs(message: telebot.types.Message) -> None:
    for i in os.listdir(f"{MAIN_PATH}"):
        if i.endswith(".pdf") or i.endswith("txt"):
            with open(f"{MAIN_PATH}{i}", "rb") as f:
                bot.send_document(message.chat.id, f)
            f.close()


@bot.message_handler(commands=['recordings'])
def send_recordings(message: telebot.types.Message) -> None:
    for file in os.listdir(f"{MAIN_PATH}recordings"):
        with open(f"{MAIN_PATH}recordings/{file}", "rb") as f:
            bot.send_voice(message.chat.id, f)
        f.close()


@bot.message_handler(commands=['adduser'])
def add_user(message: telebot.types.Message) -> None:
    if len(message.text.split()) == 2 and len(list(message.text.split()[1])) == 9 or 10:
        try:
            allowed_users.add(int(message.text.split()[1]))
            new_file = []
            with open(f"{MAIN_PATH}{CONFIG_NAME}.conf", "r") as f:
                for line in f.readlines():
                    if line.startswith("users:"):
                        for l in line.replace("\n", ",").split():
                            if l == f"{message.text.split()[1]},":
                                bot.reply_to(message, "User is already allowed!")
                                return
                        nline = line.strip() + f", {int(message.text.split()[1])}\n"
                        new_file.append(nline)
                    else:
                        new_file.append(line)
            f.close()
            with open(f"{MAIN_PATH}{CONFIG_NAME}.conf", "w") as f:
                f.writelines(new_file)
            f.close()
            bot.reply_to(message, f"Added user {message.text.split()[1]}")
            logging.info(f"Added user {message.text.split()[1]}")
        except ValueError as e:
            bot.reply_to(message, "Please enter a valid user id!")
            bot.reply_to(message, str(e))
    else:
        bot.reply_to(message, "Please enter a user id!")


@bot.message_handler(commands=['removeuser'])
def remove_user(message: telebot.types.Message) -> None:
    if len(message.text.split()) == 2 and len(list(message.text.split()[1])) == 9 or 10:
        try:
            new_file = []
            with open(f"{MAIN_PATH}{CONFIG_NAME}.conf", 'r') as f:
                for line in f.readlines():
                    if line.startswith("users:"):
                        split_line = line.replace('\n', ',').split()
                        removed = False
                        for l in split_line:
                            if l == split_line[0]:
                                nline = f"{l}"
                                continue
                            if l == f"{message.text.split()[1]},":
                                # user found and removed
                                removed = True
                                allowed_users.remove(int(message.text.split()[1]))
                                bot.reply_to(message, f"User {message.text.split()[1]} removed!")
                                logging.info(f"User {message.text.split()[1]} removed!")
                                if l == split_line[-1]:
                                    nline = list(nline)
                                    nline[-1] = '\n'
                                    nline = ''.join(nline)
                                continue
                            if l == split_line[-1]:
                                l = l.replace(',', '\n')
                            nline += f" {l}"
                        new_file.append(nline)
                        if not removed:
                            bot.reply_to(message, "User could not be found! Was the user allowed before?")
                    else:
                        new_file.append(line)
            f.close()
            with open(f"{MAIN_PATH}{CONFIG_NAME}.conf", 'w') as f:
                f.writelines(new_file)
            f.close()
        except ValueError as e:
            bot.reply_to(message, "Please enter a valid user id!")
            bot.reply_to(message, str(e))
    else:
        bot.reply_to(message, "Please enter a user id!")


@bot.message_handler(commands=['restart'])
def restart(message: telebot.types.Message) -> None:
    bot.reply_to(message, "Restarting...")
    os.system(f"systemctl restart {PERSONA_NAME}.service")


@bot.message_handler(commands=['stop'])
def stop(message: telebot.types.Message) -> None:
    bot.reply_to(message, "Stopping...")
    os.system(f"systemctl stop {PERSONA_NAME}.service")


@bot.message_handler(commands=['reboot'])
def reboot(message: telebot.types.Message) -> None:
    bot.reply_to(message, "Rebooting...")
    os.system("systemctl reboot")


@bot.message_handler(commands=['ping'])
def ping(message: telebot.types.Message) -> None:
    bot.reply_to(message, "Pong!")


# --- /COMMANDS ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        bot.reply_to(message, WELCOME_MSG)


@bot.message_handler(commands=['forget'])
def clear_context(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        if user_context.get(message.from_user.id, None) is None:
            bot.reply_to(message, NOT_FORGOTTEN_MSG)
            return
        del user_context[message.from_user.id]
        bot.reply_to(message, FORGET_MSG)
    else:
        log_unrestricted(message)


@bot.message_handler(commands=[PERSONA_NAME])
def send_message(message: telebot.types.Message, transcript: str = None) -> None:
    if message.from_user.id in allowed_users:
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
            remove_lock()
            logging.info("time taken: " + str(round(start_time - stop_time, 2)) + " seconds")
            output = message_to_list(output["content"])
            logging.info(f"Number of message chunks: {len(output)}")
            for i in output:
                bot.reply_to(message, i, parse_mode="Markdown")
        except telebot.apihelper.ApiTelegramException as e:
            error = f"Error while generating chat response: {str(e)}"
            logging.error(error)
            bot.reply_to(message, error)
            debug_msg(error)
            remove_lock()
            try:
                for i in output:
                    bot.reply_to(message, i)
            except Exception as e:
                error = f"second try due to {str(e)}"
                logging.error(error)
                bot.reply_to(message, error)
                debug_msg(error)
                remove_lock()
        except Exception as e:
            error = f"Error while generating chat response: {str(e)}"
            logging.error(error)
            bot.reply_to(message, error)
            debug_msg(error)
            remove_lock()
    else:
        log_unrestricted(message)


# --- IMAGE GENERATION ---
@bot.message_handler(commands=['generate'])
def generate(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        start_time = time.time()
        try:
            if message.text[10:] == "":
                bot.reply_to(message, "Please enter a prompt for the image generation")
                return
            bot.reply_to(message, "Your image is being drawn...")
            logging.info(
                f"{message.from_user.first_name}({message.from_user.id}): Image generation message({message.text[10:]})")
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
                bot.send_photo(message.chat.id, response.content,
                               caption=message.text[10:] + "\ntime taken for image generation: " + str(
                                   round(start_time - stop_time, 2)) + " seconds")
            except openai.error.OpenAIError as e:
                error = f"HTTP STATUS: {e.http_status}, ERROR: {e.error}"
                logging.error(error)
                bot.reply_to(message, error)
                debug_msg(error)
        except Exception as e:
            error = f"Error while generating image: {str(e)}"
            logging.error(error)
            bot.reply_to(message, error)
            debug_msg(error)
    else:
        log_unrestricted(message)


@bot.message_handler(content_types=['photo'])
def make_variation(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        start_time = time.time()
        if message.caption.lower() not in ["make variation", "make variations", "m"]:  # m is a shortcut
            return
        try:
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
            image_uuid = str(uuid.uuid4())
            with open(f"{MAIN_PATH}{image_uuid}.png", 'wb') as image:
                image.write(downloaded_file)
            image.close()
            os.system(f"convert {MAIN_PATH}{image_uuid}.png -resize 1024x1024 {MAIN_PATH}{image_uuid}.png")
            try:
                response = openai.Image.create_variation(
                    image=open(f"{MAIN_PATH}{image_uuid}.png", "rb"),
                    n=4 if more_images else NUM_IMAGES,
                    size="1024x1024"
                )
                image_url = response['data'][0]['url']
                response = requests.get(image_url)
                os.remove(f"{MAIN_PATH}{image_uuid}.png")
                stop_time = time.time()
                logging.info("time taken for image generation: " + str(round(start_time - stop_time, 2)) + " seconds")
                bot.send_photo(message.chat.id, response.content, caption="\ntime taken for image generation: " + str(
                    round(start_time - stop_time, 2)) + " seconds")
            except openai.error.OpenAIError as e:
                error = f"HTTP STATUS: {e.http_status}, ERROR: {e.error}"
                logging.error(error)
                bot.reply_to(message, error)
                debug_msg(error)
        except Exception as e:
            error = f"Error while making variation: {str(e)}"
            logging.error(error)
            bot.reply_to(message, error)
            debug_msg(error)
    else:
        log_unrestricted(message)


# --- AUDIO TRANSCRIPTION ---
@bot.message_handler(content_types=['voice'])
def voice_processing(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        start_time = time.time()
        try:
            file_info = bot.get_file(message.voice.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            audio_uuid = str(uuid.uuid4())
            with open(f"{MAIN_PATH}{audio_uuid}.ogg", 'wb') as audio_file:
                audio_file.write(downloaded_file)
            audio_file.close()
            os.system(
                f"ffmpeg -i {MAIN_PATH}{audio_uuid}.ogg -codec:a libmp3lame -qscale:a 2 {MAIN_PATH}{audio_uuid}.mp3")
            with open(f"{MAIN_PATH}{audio_uuid}.mp3", 'rb') as audio_file:
                transcript = openai.Audio.transcribe("whisper-1", audio_file)
                if message.forward_from is not None:
                    bot.send_message(message.chat.id, transcript["text"])
                else:
                    send_message(message, transcript["text"])
            audio_file.close()
            os.system(
                f"mv {MAIN_PATH}{audio_uuid}.mp3 {MAIN_PATH}recordings/{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp3")
            os.remove(f"{MAIN_PATH}{audio_uuid}.ogg")
        except Exception as e:
            error = f"Error while processing audio: {str(e)}"
            logging.error(error)
            bot.reply_to(message, error)
            debug_msg(error)
        stop_time = time.time()
        logging.info(
            f"User {message.from_user.first_name}({message.from_user.id}) accessed voice processing. time taken: {str(round(start_time - stop_time, 2))} seconds")
    else:
        log_unrestricted(message)


# --- TRANSLATION ---
def deepl_translate(message: telebot.types.Message, text: str, target_lang: str, reply=True) -> str:
    url = 'https://api-free.deepl.com/v2/translate'
    payload = {'text': text, 'target_lang': target_lang}
    headers = {'Authorization': "DeepL-Auth-Key " + deepl_api_key,
               'User-Agent': 'YourApp/1.2.3',
               'Content-Type': 'application/x-www-form-urlencoded'}

    response = requests.post(url, data=payload, headers=headers)
    res = json.loads(response.text)
    logging.info(f"Translated text for {message.from_user.first_name}({message.from_user.id}): {res['translations'][0]['text']}")
    if reply:
        output = message_to_list(res["translations"][0]["text"])
        for i in output:
            bot.reply_to(message, i)
    return res["translations"][0]["text"]


def translate_message(message: telebot.types.Message, text: str, target_lang: str) -> None:
    if message.from_user.id in allowed_users:
        deepl_translate(message, text[4:], target_lang)
    else:
        log_unrestricted(message)


@bot.message_handler(commands=['tge'])
def translate_message_to_german(message: telebot.types.Message) -> None:
    translate_message(message, message.text, "DE")


@bot.message_handler(commands=['ten'])
def translate_message_to_english(message: telebot.types.Message) -> None:
    translate_message(message, message.text, "EN")


@bot.message_handler(commands=['tfr'])
def translate_message_to_english(message: telebot.types.Message) -> None:
    translate_message(message, message.text, "FR")


@bot.message_handler(commands=['tes'])
def translate_message_to_english(message: telebot.types.Message) -> None:
    translate_message(message, message.text, "ES")


@bot.message_handler(commands=['tpl'])
def translate_message_to_english(message: telebot.types.Message) -> None:
    translate_message(message, message.text, "PL")


@bot.message_handler(content_types=['video'])
def translate_video(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        start_time = time.time()
        try:
            bot.reply_to(message, "Translating video...")
            file_info = bot.get_file(message.video.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_uuid = str(uuid.uuid4())
            with open(f"{MAIN_PATH}{file_uuid}.mp4", 'wb') as video_file:
                video_file.write(downloaded_file)
            video_file.close()
            os.system(f"ffmpeg -i {MAIN_PATH}{file_uuid}.mp4 {MAIN_PATH}{file_uuid}.mp3")
            os.remove(f"{MAIN_PATH}{file_uuid}.mp4")
            with open(f"{MAIN_PATH}{file_uuid}.mp3", 'rb') as audio_file:
                transcript = openai.Audio.translate("whisper-1", audio_file)
            audio_file.close()
            os.remove(f"{MAIN_PATH}{file_uuid}.mp3")
            if message.caption is not None:
                if message.caption.lower() in ["tg", "translate to german"]:
                    deepl_translate(message, transcript["text"], "DE")
                elif message.caption.lower() in ["tf", "translate to french"]:
                    deepl_translate(message, transcript["text"], "FR")
                elif message.caption.lower() in ["ts", "translate to spanish"]:
                    deepl_translate(message, transcript["text"], "ES")
                elif message.caption.lower() in ["tp", "translate to polish"]:
                    deepl_translate(message, transcript["text"], "PL")
                else:
                    logging.info(f"Translated video text for {message.from_user.first_name}({message.from_user.id}): {transcript['text']}")
                    output = message_to_list(transcript["text"])
                    for i in output:
                        bot.reply_to(message, i)
            else:
                logging.info(f"Translated video text for {message.from_user.first_name}({message.from_user.id}): {transcript['text']}")
                output = message_to_list(transcript["text"])
                for i in output:
                    bot.reply_to(message, i)
        except Exception as e:
            error = f"Error while translating video: {str(e)}"
            logging.error(error)
            bot.reply_to(message, error)
            debug_msg(error)
        stop_time = time.time()
        logging.info("time taken for video translation: " + str(round(start_time - stop_time, 2)) + " seconds")
    else:
        log_unrestricted(message)


def translate_to_document(message: telebot.types.Message, text: str, target_lang: str) -> None:
    logging.info(f"Extracted text for {message.from_user.first_name}({message.from_user.id}): {text}")
    translated_text = deepl_translate(message, text, target_lang, reply=False)
    file_uuid = str(uuid.uuid4())
    with open(f"{MAIN_PATH}{file_uuid}.txt", 'w') as doc:
        doc.write(translated_text)
    doc.close()
    os.system(f"pandoc {MAIN_PATH}{file_uuid}.txt -o {MAIN_PATH}{file_uuid}.pdf")
    with open(f"{MAIN_PATH}{file_uuid}.pdf", "rb") as f:
        bot.send_document(message.chat.id, f)
    f.close()
    os.remove(f"{MAIN_PATH}{file_uuid}.txt")
    os.remove(f"{MAIN_PATH}{file_uuid}.pdf")


# @bot.message_handler(content_types=['document'])
def translate_document(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        start_time = time.time()
        file_type = message.document.mime_type.split('/')[1]
        logging.info(f"Translating document with file type: {file_type} for {message.from_user.first_name}({message.from_user.id})")
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        # file_uuid = str(uuid.uuid4())
        if file_type == "plain":
            pass
            # file_type = "txt"
        # with open(f"{MAIN_PATH}{file_uuid}.{file_type}", 'w') as doc:
        #     doc.write()
        # doc.close()
        translate_to_document(message, str(downloaded_file)[2:-1], "DE")
        # if file_type == "txt":
        #     with open(f"{MAIN_PATH}{file_uuid}.txt", 'r') as doc:
        #         text = doc.read()
        #         translate_to_document(message, text, "DE")
        #     doc.close()
        # elif file_type == "pdf":
        #     with open(f"{MAIN_PATH}{file_uuid}.pdf", 'rb') as doc:
        #         text = slate.PDF(doc)
        #     translate_to_document(message, text, "DE")
        # elif file_type is not None:
        #     bot.reply_to(message, f"Unsupported file type: {file_type}. Reach out to https://t.me/earth_down for support.")
        #     logging.info(f"Unsupported file type: {file_type} for {message.from_user.first_name}({message.from_user.id})")
        # os.remove(f"{MAIN_PATH}{file_uuid}.{file_type}")
        stop_time = time.time()
        logging.info("time taken for document translation: " + str(round(start_time - stop_time, 2)) + " seconds")
    else:
        log_unrestricted(message)


# --- TEXT TO SPEECH ---
def tts_fn(message: telebot.types.Message, text: str, language_code: str, voice_name: str, gender: tts.SsmlVoiceGender) -> None:
    if message.from_user.id in allowed_users:
        if text == "":
            bot.reply_to(message, "Please provide text to be spoken.")
            return
        start_time = time.time()
        for i in message_to_list(text):
            try:
                text_input = tts.SynthesisInput(text=i)
                # noinspection PyTypeChecker
                voice_params = tts.VoiceSelectionParams(language_code=language_code, name=voice_name, ssml_gender=gender)
                # noinspection PyTypeChecker
                audio_config = tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3)

                client = tts.TextToSpeechClient()
                response = client.synthesize_speech(input=text_input, voice=voice_params, audio_config=audio_config)
                generated_audio_uuid = str(uuid.uuid4())
                filename = f"{MAIN_PATH}generated-audio/{generated_audio_uuid}.mp3"
                with open(filename, 'wb') as out:
                    out.write(response.audio_content)
                    bot.send_voice(message.chat.id, response.audio_content)
                out.close()
            except Exception as e:
                error = f"Error while generating speech: {str(e)}"
                logging.error(error)
                bot.reply_to(message, error)
                debug_msg(error)
        stop_time = time.time()
        logging.info(
            f"User {message.from_user.first_name}({message.from_user.id}) accessed speech generation. time taken: {str(round(start_time - stop_time, 2))}")
    else:
        log_unrestricted(message)


@bot.message_handler(commands=['ttsge'])
def ttsg(message: telebot.types.Message, text: str = None) -> None:
    tts_fn(message, text if text else message.text[7:], "de-DE", "de-DE-Neural2-C", tts.SsmlVoiceGender.FEMALE)


@bot.message_handler(commands=['ttspl'])
def ttspl(message: telebot.types.Message, text: str = None) -> None:
    tts_fn(message, text if text else message.text[7:], "pl-PL", "pl-PL-Standard-D", tts.SsmlVoiceGender.FEMALE)


@bot.message_handler(commands=['ttsfr'])
def ttsfr(message: telebot.types.Message, text: str = None) -> None:
    tts_fn(message, text if text else message.text[7:], "fr-FR", "fr-FR-Neural2-C", tts.SsmlVoiceGender.FEMALE)


@bot.message_handler(commands=['ttses'])
def ttses(message: telebot.types.Message, text: str = None) -> None:
    tts_fn(message, text if text else message.text[7:], "es-ES", "es-US-News-F", tts.SsmlVoiceGender.FEMALE)


@bot.message_handler(commands=['ttsen'])
def ttsen(message: telebot.types.Message, text: str = None) -> None:
    tts_fn(message, text if text else message.text[7:], "en-US", "en-US-Standard-F", tts.SsmlVoiceGender.FEMALE)


# --- YOUTUBE AUDIO ---
def yt_audio(message: telebot.types.Message) -> None:
    file_uuid = str(uuid.uuid4())
    try:
        start_time = time.time()
        logging.info(f"User {message.from_user.first_name}({message.from_user.id}) accessed youtube audio with the following link: {message.text[10:]}.")
        ydl_opts = {'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'outtmpl': f"{MAIN_PATH}{file_uuid}.%(ext)s"}
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([message.text[10:]])
        bot.send_audio(message.chat.id, open(f"{MAIN_PATH}{file_uuid}.mp3", 'rb'))
        os.remove(f"{MAIN_PATH}{file_uuid}.mp3")
        stop_time = time.time()
        logging.info("time taken for youtube audio: " + str(round(start_time - stop_time, 2)) + " seconds")
    except Exception as e:
        error = f"Error while downloading youtube audio: {str(e)}"
        logging.error(error)
        bot.reply_to(message, error)
        debug_msg(error)


# --- YOUTUBE DOWNLOAD ---
def yt_download(message: telebot.types.Message) -> None:
    file_uuid = str(uuid.uuid4())
    try:
        start_time = time.time()
        logging.info(f"User {message.from_user.first_name}({message.from_user.id}) accessed youtube download with the following link: {message.text[13:]}.")
        ydl_opts = {'format': 'bestvideo[ext=mp4]+bestaudio[ext=mp3]/best[ext=mp4]/best', 'outtmpl': f"{MAIN_PATH}{file_uuid}.%(ext)s"}
        bot.reply_to(message, f"Downloading youtube video {message.text[13:]} ...")
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download([message.text[13:]])
        bot.send_video(message.chat.id, open(f"{MAIN_PATH}{file_uuid}.mp4", 'rb'))
        os.remove(f"{MAIN_PATH}{file_uuid}.mp4")
        stop_time = time.time()
        logging.info(f"User {message.from_user.first_name}({message.from_user.id}) downloaded youtube video. time taken: {str(round(start_time - stop_time, 2))}")
    except Exception as e:
        error = f"Error while downloading youtube video: {str(e)}"
        logging.error(error)
        bot.reply_to(message, error)
        debug_msg(error)
        if os.path.exists(f"{MAIN_PATH}{file_uuid}.mp4"):
            os.remove(f"{MAIN_PATH}{file_uuid}.mp4")
        if os.path.exists(f"{MAIN_PATH}{file_uuid}.mp3"):
            os.remove(f"{MAIN_PATH}{file_uuid}.mp3")


# --- YOUTUBE TRANSCRIPTION ---
@bot.message_handler(commands=['yt'])
def yt(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        if message.text[4:12] == "download":
            yt_download(message)
            return
        elif message.text[4:9] == "audio":
            yt_audio(message)
            return
        elif message.text[4:] == "" and not message.text[4:].startswith("https://www.youtube.com/watch?v="):
            bot.reply_to(message, "Please provide a youtube link.")
            return
        start_time = time.time()
        try:
            yt_id = message.text.split("?v=")[1]
            logging.info(
                f"User {message.from_user.first_name}({message.from_user.id}) accessed youtube transcription with youtube id: {yt_id}.")
            transcript = YouTubeTranscriptApi.get_transcript(yt_id, languages=["de", "en", "pl", "es", "fr"])
            text = ""
            for chunk in transcript:
                text += chunk['text'] + " "
            file_uuid = str(uuid.uuid4())
            with open(f"{MAIN_PATH}{file_uuid}.txt", 'w') as f:
                f.write(text)
            f.close()
            os.system(f"pandoc {MAIN_PATH}{file_uuid}.txt -o {MAIN_PATH}{file_uuid}.pdf")
            with open(f"{MAIN_PATH}{file_uuid}.pdf", "rb") as f:
                bot.send_document(message.chat.id, f)
            f.close()
            os.remove(f"{MAIN_PATH}{file_uuid}.txt")
            os.remove(f"{MAIN_PATH}{file_uuid}.pdf")
            # lang = langid.classify(text)[0]
            # bot.reply_to(message, f"Language detected: {lang}")
            # if lang == "en":
            #     ttsen(message, text)
            # elif lang == "de":
            #     ttsg(message, text)
            # elif lang == "fr":
            #     ttsfr(message, text)
            # elif lang == "es":
            #     ttses(message, text)
            # elif lang == "pl":
            #     ttspl(message, text)
            if len(ENCODING.encode(text)) < 3000:
                send_message(message, "summarize this text:" + text)
            else:
                bot.send_message(message.chat.id, "Transcript too long for summarization.")
            stop_time = time.time()
            logging.info(f"User {message.from_user.first_name}({message.from_user.id}) accessed youtube transcription. time taken: {str(round(start_time - stop_time, 2))}")
        except Exception as e:
            error = f"Error while generating youtube transcription: {str(e)}"
            logging.error(error)
            bot.reply_to(message, error)
            debug_msg(error)
    else:
        log_unrestricted(message)


@bot.message_handler(func=lambda message: True)
def handle_default(message: telebot.types.Message) -> None:
    if message.from_user.id in allowed_users:
        if message.video is not None:
            translate_video(message)
        else:
            send_message(message)
    else:
        log_unrestricted(message)


# --- UNRESTRICTED ACCESS ---
def log_unrestricted(message: telebot.types.Message) -> None:
    if message.from_user.id not in already_restriced_users:
        bot.reply_to(message, "You are not allowed to use me! You can ask https://t.me/earth_down for permission. Your user meta data and all messages are logged!")
        already_restriced_users.add(message.from_user.id)
    while not lock():
        time.sleep(1)
    logging.warning(str(message))
    remove_lock()
    debug_msg("A stranger tried to use me:\n" + str(message))


# --- DEBUG ---
def debug_msg(msg: str) -> None:
    if DEBUG:
        for admin in admins:
            bot.send_message(admin, msg)


# --- LOCKING ---
def lock() -> bool:
    try:
        os.mkdir(LOCK_DIR)
    except FileExistsError:
        return False
    return True


# --- REMOVE LOCK ---
def remove_lock() -> None:
    try:
        if os.path.exists(LOCK_DIR):
            os.rmdir(LOCK_DIR)
    except Exception as e:
        logging.critical(f"Cannot delete lock dir '{LOCK_DIR}': {e}")


# --- Start bot ---
bot.polling()
