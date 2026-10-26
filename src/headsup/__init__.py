import logging

# httpx logs every request URL at INFO ("HTTP Request: POST https://api.telegram.org/bot<TOKEN>/...").
# The Telegram bot token and the secret calendar address live in URLs, so keep these loggers at WARNING
# whatever the application's logging level is.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
