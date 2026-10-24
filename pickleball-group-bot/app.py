import json
import os
from functools import wraps
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

from cards import checkin_card, delete_confirm_card, guest_choice_card, list_carousel, poll_card
from db import Database
from polls import LIST_COMMAND, headcount, next_guest_state, split_roster, TEMPLATE, TRIGGER, is_trigger, parse_poll

app = Flask(__name__)
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"])
configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
db = Database()

HELP = (
    "🏓 匹克球揪團小幫手\n"
    f"在群組輸入以「{TRIGGER}」開頭的訊息，就會建立報名卡片：\n\n"
    f"{TEMPLATE}\n\n"
    "大家按卡片上的「參加／不參加」即可報名，名單會自動更新。\n"
    "要帶群組以外的朋友，按「➕ 攜伴」，每按一次多算 1 人。\n"
    "到場後按「📍 到場簽到」，會顯示實到人數與每人應付的場地費。\n"
    f"輸入「{LIST_COMMAND}」可查看所有揪團場次，也可以在那裡刪除場次。"
)
GONE = "⚠️ 這場揪團已經被刪除了。輸入「場次」查看目前的揪團。"


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


def flex(contents, alt):
    return FlexMessage(alt_text=alt, contents=FlexContainer.from_json(json.dumps(contents)))


def card_message(poll, note=None):
    bubble, alt = poll_card(poll, db.get_votes(poll["id"]), note)
    return flex(bubble, alt)


def waitlist_note(poll, user_id):
    """如果這個人（含攜伴）在候補名單中，回傳提示文字。"""
    _, waitlist, _ = split_roster(db.get_votes(poll["id"]), poll["capacity"])
    return "，名額不足排入候補" if any(v["user_id"] == user_id for v in waitlist) else ""


def registered_guests(poll, user_id):
    """這個人報名參加時登記的攜伴人數（沒報名或不參加為 0）。"""
    vote = db.get_vote(poll["id"], user_id)
    return vote["guests"] if vote and vote["choice"] == "yes" else 0


def checkin_message(poll, note=None):
    bubble, alt = checkin_card(poll, db.get_votes(poll["id"]), db.get_checkins(poll["id"]), note)
    return flex(bubble, alt)


def list_messages(chat_id):
    polls = db.list_polls(chat_id)
    if not polls:
        return [TextMessage(text=f"目前沒有揪團場次。\n輸入「{TRIGGER}」查看如何建立揪團。")]
    carousel, alt = list_carousel(polls)
    messages = []
    if len(polls) > 12:
        messages.append(TextMessage(text=f"共 {len(polls)} 場揪團，以下顯示最新的 12 場。"))
    messages.append(flex(carousel, alt))
    return messages


def once(func):
    """同一個 LINE 事件只處理一次。

    Render 回應太慢時，LINE 會重送同一個事件（Webhook redelivery），
    例如按一次「➕ 攜伴」卻被加好幾次。每個事件都有唯一的 webhookEventId，
    處理過的就直接略過。"""
    @wraps(func)
    def wrapper(event):
        event_id = getattr(event, "webhook_event_id", None)
        if not event_id:
            return func(event)
        if not db.claim_event(event_id):
            app.logger.info("略過重複的事件：%s", event_id)
            return None
        try:
            return func(event)
        except Exception:
            db.release_event(event_id)
            raise
    return wrapper


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
@once
def on_join(event):
    """機器人被邀請進群組時，送出使用說明。"""
    with ApiClient(configuration) as client:
        reply(MessagingApi(client), event.reply_token, [TextMessage(text=HELP)])


@handler.add(MessageEvent, message=TextMessageContent)
@once
def on_text(event):
    text = event.message.text
    if text.strip() == LIST_COMMAND:
        with ApiClient(configuration) as client:
            reply(MessagingApi(client), event.reply_token, list_messages(chat_id_of(event.source)))
        return
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
@once
def on_postback(event):
    data = {k: v[0] for k, v in parse_qs(event.postback.data).items()}
    action = data.get("action")
    if action not in ("vote", "guest", "show", "ask_delete", "delete", "cancel",
                      "checkin_start", "checkin", "checkin_cancel") or not data.get("poll", "").isdigit():
        return

    src = event.source
    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        token = event.reply_token
        poll = db.get_poll(int(data["poll"]))

        if not poll:  # 已被刪除的場次，舊卡片上的按鈕仍可能被按到
            if action != "cancel":
                reply(api, token, [TextMessage(text=GONE)])
            return
        if poll["chat_id"] != chat_id_of(src):
            return  # 不是這個群組的揪團

        if action == "vote" and data.get("choice") in ("yes", "no"):
            name = display_name(api, src)
            db.vote(poll["id"], src.user_id, name, data["choice"])
            label = "參加" if data["choice"] == "yes" else "不參加"
            label += waitlist_note(poll, src.user_id)
            reply(api, token, [card_message(poll, f"最新名單（{name} {label}）")])

        elif action == "guest" and data.get("delta") in ("1", "-1"):
            name = display_name(api, src)
            current = db.get_vote(poll["id"], src.user_id)
            choice, guests, keep_order, msg = next_guest_state(current, int(data["delta"]))
            if choice:
                db.set_vote(poll["id"], src.user_id, name, choice, guests, keep_order)
                msg += waitlist_note(poll, src.user_id)
            reply(api, token, [card_message(poll, f"最新名單（{name} {msg}）")])

        elif action == "checkin_start":
            # 有攜伴 → 先選實到幾位同伴；沒有攜伴 → 直接簽到
            registered = registered_guests(poll, src.user_id)
            current = db.get_checkin(poll["id"], src.user_id)
            if registered:
                bubble, alt = guest_choice_card(poll, registered, current and current["guests"])
                reply(api, token, [flex(bubble, alt)])
            elif current:
                reply(api, token, [checkin_message(poll, f"{current['display_name']} 已經簽到過了")])
            else:
                name = display_name(api, src)
                db.checkin(poll["id"], src.user_id, name, 0)
                reply(api, token, [checkin_message(poll, f"{name} 簽到成功")])

        elif action == "checkin" and data.get("g", "").isdigit():
            guests = min(int(data["g"]), registered_guests(poll, src.user_id))
            name = display_name(api, src)
            db.checkin(poll["id"], src.user_id, name, guests)
            who = f"{name}（本人＋{guests} 位）" if guests else name
            reply(api, token, [checkin_message(poll, f"{who} 簽到成功")])

        elif action == "checkin_cancel":
            current = db.get_checkin(poll["id"], src.user_id)
            if current:
                db.cancel_checkin(poll["id"], src.user_id)
                note = f"{current['display_name']} 已取消簽到"
            else:
                note = "你還沒有簽到"
            reply(api, token, [checkin_message(poll, note)])

        elif action == "show":
            reply(api, token, [card_message(poll, "目前名單")])

        elif action == "ask_delete":
            votes = db.get_votes(poll["id"])
            bubble, alt = delete_confirm_card(poll, headcount(v for v in votes if v["choice"] == "yes"))
            reply(api, token, [flex(bubble, alt)])

        elif action == "delete":
            db.delete_poll(poll["id"])
            name = display_name(api, src)
            msg = f"🗑 {name} 已刪除揪團：{poll['date']} {poll['time']} {poll['venue']}"
            reply(api, token, [TextMessage(text=msg)])

        elif action == "cancel":
            reply(api, token, [TextMessage(text="已取消，場次沒有被刪除。")])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
