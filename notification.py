import html
import re

import requests

# Shared, live configuration object (updated when settings are saved in the UI)
from config import config

REQUEST_TIMEOUT = 10  # seconds


def _setting(option):
    """Return a telegram setting, or '' if unset or still the sample placeholder."""
    value = config.get('telegram', option, fallback='').strip()
    if not value or re.fullmatch(r'X+', value):
        return ''
    return value


def telegram_sendNotification(notification):
    token = _setting('telegram_token')
    chat_id = _setting('telegram_chatid')
    if not (token and chat_id):
        return False

    payload = {
        'chat_id': chat_id,
        'text': html.escape(str(notification), quote=False),
        'parse_mode': 'HTML'
    }
    try:
        return requests.post(
            "https://api.telegram.org/bot{token}/sendMessage".format(token=token),
            data=payload,
            timeout=REQUEST_TIMEOUT
        ).content
    except requests.exceptions.RequestException as e:
        print('Telegram notification failed: {}'.format(type(e).__name__))
        return False
