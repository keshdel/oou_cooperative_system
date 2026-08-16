"""Expo push notification delivery for registered CoopMS mobile devices."""

import json
import os
import threading
import urllib.request

EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'


def _post_expo_messages(messages):
    data = json.dumps(messages).encode('utf-8')
    request = urllib.request.Request(
        EXPO_PUSH_URL,
        data=data,
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def _send(messages):
    try:
        _post_expo_messages(messages)
    except Exception:
        pass


def send_mobile_pushes(db, user_id, title, message, notification_type='info', action_url=''):
    """Send push notifications to active mobile devices for a user. Never raises."""
    if os.environ.get('MOBILE_PUSH_ENABLED', '1').lower() in {'0', 'false', 'no'}:
        return
    if not user_id:
        return
    try:
        rows = db.execute(
            'SELECT push_token FROM mobile_devices WHERE user_id = ? AND COALESCE(enabled, 1) = 1',
            (user_id,),
        ).fetchall()
        tokens = [r['push_token'] for r in rows if r['push_token']]
        if not tokens:
            return
        messages = [
            {
                'to': token,
                'title': title,
                'body': message,
                'sound': 'default',
                'data': {
                    'type': notification_type,
                    'action_url': action_url or '',
                },
            }
            for token in tokens
        ]
        if os.environ.get('MOBILE_PUSH_SYNC') == '1':
            _send(messages)
        else:
            threading.Thread(target=_send, args=(messages,), daemon=True).start()
    except Exception:
        pass
