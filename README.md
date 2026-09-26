# Telegram Bot

A Python Telegram bot that connects to OpenAI and supports configurable persona behavior, access control, translations, YouTube helpers, transcription, and text-to-speech.

## Quick Start

1. Copy `bot.conf.example` to `<name>.conf` and fill in your settings.
2. Set required environment variables:
   - `OPENAI_API_KEY`
   - `DEEPL_API_KEY` (required for translation commands)
3. Install dependencies in your Python environment.
4. Run the bot:

```bash
python3.14 telegram_bot.py /path/to/project/ <name>
```

Notes:

- The first argument must be the bot working directory (usually the project path) and should end with `/`.
- The second argument is the config filename without `.conf`.

## License

This project is licensed under the MIT License.
