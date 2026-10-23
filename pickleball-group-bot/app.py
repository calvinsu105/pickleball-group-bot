import json
import os
from urllib.parse import parse_qs

from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    FlexContainer,
    FlexMessage,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    JoinEvent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
)

from cards import poll_card
from db import Database
from polls import TEMPLATE, TRIGGER, is_trigger, parse_poll

app = Flask(__name__)
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
db = Database()

HELP = (
    "🏓 匹克球揪團小幫手\n"
    f"在群組輸入以「{TRIGGER}」開頭的訊息，就會建立報名卡片：\n\n"
    f"{TEMPLATE}\n\n"
    "大家按卡片上的「參加／不參加」即可報名，名單會自動更新。"
)


# ---------- 小工具 ----------
def chat_id_of(source):
    return getattr(source, "group_id", None) or getattr(source, "room_id", None) or source.user_id


def display_name(api, source):
    """取得使用者在群組中的名稱（不需要先加機器人好友）。"""
    uid = source.user_id
    try:
        if source.type == "group":
            return api.get_group_member_profile(source.group_id, uid).display_name
        if source.type == "room":
            return api.get_room_member_profile(source.room_id, uid).display_name
        return api.get_profile(uid).display_name
    except Exception:
        app.logger.warning("無法取得使用者名稱：%s", uid, exc_info=True)
        return "成員"


def reply(api, token, messages):
    api.reply_message(ReplyMessageRequest(reply_token=token, messages=messages))


def card_message(poll, note=None):
    bubble, alt = poll_card(poll, db.get_votes(poll["id"]), note)
    return FlexMessage(alt_text=alt, contents=FlexContainer.from_json(json.dumps(bubble)))


# ---------- Webhook ----------
@app.get("/")
def health():
    return "Pickleball group bot is running."


@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(JoinEvent)
def on_join(event):
    """機器人被邀請進群組時，送出使用說明。"""
    with ApiClient(configuration) as client:
        reply(MessagingApi(client), event.reply_token, [TextMessage(text=HELP)])


@handler.add(MessageEvent, message=TextMessageContent)
def on_text(event):
    text = event.message.text
    if not is_trigger(text):
        return  # 群組內其他聊天一律不回應

    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        fields, missing = parse_poll(text)

        if len(missing) == 4:
            # 只打「揪團」→ 給範本；其他像「揪團嗎？」的一般聊天 → 不打擾
            if text.strip() == TRIGGER:
                reply(api, event.reply_token, [TextMessage(text=HELP)])
            return
        if missing:
            msg = f"⚠️ 揪團資訊缺少：{'、'.join(missing)}\n請照以下格式重新輸入：\n\n{TEMPLATE}"
            reply(api, event.reply_token, [TextMessage(text=msg)])
            return

        src = event.source
        poll_id = db.create_poll(chat_id_of(src), src.user_id, display_name(api, src), fields)
        reply(api, event.reply_token, [card_message(db.get_poll(poll_id), "報名開始！請按下方按鈕")])


@handler.add(PostbackEvent)
def on_postback(event):
    data = {k: v[0] for k, v in parse_qs(event.postback.data).items()}
    if data.get("action") != "vote" or data.get("choice") not in ("yes", "no"):
        return

    poll = db.get_poll(int(data["poll"]))
    src = event.source
    if not poll or poll["chat_id"] != chat_id_of(src):
        return  # 找不到或不是這個群組的揪團

    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        name = display_name(api, src)
        db.vote(poll["id"], src.user_id, name, data["choice"])

        action = "參加" if data["choice"] == "yes" else "不參加"
        reply(api, event.reply_token, [card_message(poll, f"最新名單（{name} {action}）")])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
