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
    MentionSubstitutionObject,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    TextMessageV2,
    UserMentionTarget,
)
from linebot.v3.webhooks import (
    JoinEvent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
)

from cards import checkin_card, delete_confirm_card, guest_choice_card, list_carousel, poll_card
from db import Database
from polls import LIST_COMMAND, amount_due, build_mentions, headcount, next_guest_state, promoted_users, split_roster, TEMPLATE, TRIGGER, is_trigger, parse_poll

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


def mention_messages(header, entries, footer=""):
    """把 build_mentions 的結果轉成 LINE 的 tag 訊息（Text message v2）。"""
    return [
        TextMessageV2(text=text, substitution={
            key: MentionSubstitutionObject(mentionee=UserMentionTarget(user_id=uid))
            for key, uid in subs.items()
        })
        for text, subs in build_mentions(header, entries, footer)
    ]


def going_now(poll):
    going, _, _ = split_roster(db.get_votes(poll["id"]), poll["capacity"])
    return going


def promotion_messages(poll, before_going, actor_id):
    """有人因為別人取消或減少攜伴而從候補補上時，tag 通知他們。"""
    promoted = promoted_users(before_going, going_now(poll), actor_id)
    if not promoted:
        return []
    header = f"🎉 候補遞補成功！{poll['date']} {poll['time']} {poll['venue']}"
    return mention_messages(header, [(v["user_id"], "") for v in promoted], "已從候補改為正取，請準時到場。")


def guest_text(n):
    return f"（＋{n} 位同伴）" if n else ""


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


POSTBACK_ACTIONS = ("vote", "guest", "show", "ask_delete", "delete", "cancel",
                    "checkin_start", "checkin", "checkin_cancel", "pay", "unpay",
                    "notify_going", "notify_checkin", "notify_pay")
NEEDS_NAME = ("vote", "guest", "checkin_start", "checkin", "delete")


@handler.add(PostbackEvent)
@once
def on_postback(event):
    data = {k: v[0] for k, v in parse_qs(event.postback.data).items()}
    action = data.get("action")
    if action not in POSTBACK_ACTIONS or not data.get("poll", "").isdigit():
        return

    src = event.source
    with ApiClient(configuration) as client:
        api = MessagingApi(client)
        # 1. 先向 LINE 查名稱（網路呼叫放在鎖外面，不讓其他人等）
        name = display_name(api, src) if action in NEEDS_NAME else None
        # 2. 鎖住這場揪團：讀 → 算 → 寫 → 組回覆，全部在同一筆交易內完成
        with db.poll_lock(int(data["poll"])):
            messages = postback_messages(action, data, src, name)
        # 3. 交易完成、鎖放開後才回覆 LINE
        if messages:
            reply(api, event.reply_token, messages)


def postback_messages(action, data, src, name):
    """處理按鈕事件並回傳要回覆的訊息。只讀寫資料庫，不呼叫 LINE API。"""
    poll = db.get_poll(int(data["poll"]))
    if not poll:  # 已被刪除的場次，舊卡片上的按鈕仍可能被按到
        return [] if action == "cancel" else [TextMessage(text=GONE)]
    if poll["chat_id"] != chat_id_of(src):
        return []  # 不是這個群組的揪團
    pid, uid = poll["id"], src.user_id

    if action == "vote" and data.get("choice") in ("yes", "no"):
        before = going_now(poll)
        db.vote(pid, uid, name, data["choice"])
        label = "參加" if data["choice"] == "yes" else "不參加"
        label += waitlist_note(poll, uid)
        return [card_message(poll, f"最新名單（{name} {label}）")] + promotion_messages(poll, before, uid)

    if action == "guest" and data.get("delta") in ("1", "-1"):
        current = db.get_vote(pid, uid)
        choice, guests, keep_order, msg = next_guest_state(current, int(data["delta"]))
        before = going_now(poll)
        if choice:
            db.set_vote(pid, uid, name, choice, guests, keep_order, current=current)
            msg += waitlist_note(poll, uid)
        return [card_message(poll, f"最新名單（{name} {msg}）")] + promotion_messages(poll, before, uid)

    if action == "notify_going":
        going = going_now(poll)
        if not going:
            return [TextMessage(text="目前沒有正取的參加者。")]
        header = f"📣 揪團提醒：{poll['date']} {poll['time']}\n📍 {poll['venue']}"
        entries = [(v["user_id"], guest_text(v.get("guests") or 0)) for v in going]
        return mention_messages(header, entries, "請準時到場，到了記得按「📍 到場簽到」！")

    if action == "notify_checkin":
        checked = {c["user_id"] for c in db.get_checkins(pid)}
        missing = [v for v in going_now(poll) if v["user_id"] not in checked]
        if not missing:
            return [TextMessage(text="✅ 正取的人都已經簽到了。")]
        header = f"📍 還沒簽到：{poll['date']} {poll['time']} {poll['venue']}"
        return mention_messages(header, [(v["user_id"], "") for v in missing],
                                "到場後請按簽到表上的「📍 簽到」；沒來的話不用理會。")

    if action == "notify_pay":
        checkins = db.get_checkins(pid)
        unpaid = [c for c in checkins if c.get("paid_amount") is None]
        if not unpaid:
            return [TextMessage(text="✅ 已簽到的人都付款了。" if checkins else "還沒有人簽到，無法計算金額。")]
        entries = []
        for c in unpaid:
            due = amount_due(poll["price"], checkins, c)
            entries.append((c["user_id"], (f"　{due:,} 元" if due is not None else "") + guest_text(c["guests"])))
        header = f"💰 場地費提醒：{poll['date']} {poll['venue']}"
        return mention_messages(header, entries, "匯款後請按「💰 我已付款」，並自行保存匯款證明。")

    if action == "checkin_start":
        # 有攜伴 → 先選實到幾位同伴；沒有攜伴 → 直接簽到
        registered = registered_guests(poll, uid)
        current = db.get_checkin(pid, uid)
        if registered:
            bubble, alt = guest_choice_card(poll, registered, current and current["guests"])
            return [flex(bubble, alt)]
        if current:
            return [checkin_message(poll, f"{current['display_name']} 已經簽到過了")]
        db.checkin(pid, uid, name, 0)
        return [checkin_message(poll, f"{name} 簽到成功")]

    if action == "checkin" and data.get("g", "").isdigit():
        guests = min(int(data["g"]), registered_guests(poll, uid))
        db.checkin(pid, uid, name, guests)
        who = f"{name}（本人＋{guests} 位）" if guests else name
        return [checkin_message(poll, f"{who} 簽到成功")]

    if action == "checkin_cancel":
        current = db.get_checkin(pid, uid)
        if not current:
            return [checkin_message(poll, "你還沒有簽到")]
        db.cancel_checkin(pid, uid)
        note = f"{current['display_name']} 已取消簽到"
        if current.get("paid_amount") is not None:
            note += "（付款紀錄也一併取消）"
        return [checkin_message(poll, note)]

    if action == "pay":
        current = db.get_checkin(pid, uid)
        if not current:
            return [checkin_message(poll, "請先按「📍 簽到」再按已付款")]
        due = amount_due(poll["price"], db.get_checkins(pid), current)
        who = current["display_name"]
        paid = current.get("paid_amount")
        if paid is not None:
            # 已付款就不覆蓋金額，保留實際匯款時記下的數字，方便查帳
            note = f"{who} 已經付過款了"
            if due is not None and paid != due:
                diff = paid - due
                note += f"（已付 {paid:,} 元，現應付 {due:,} 元，" + \
                    (f"多付 {diff:,} 元" if diff > 0 else f"還差 {-diff:,} 元") + "，請與團主確認）"
            return [checkin_message(poll, note)]
        db.set_paid(pid, uid, due if due is not None else 0)
        amount = f" {due:,} 元" if due is not None else ""
        return [checkin_message(poll, f"{who} 已付款{amount}，請自行保存匯款證明")]

    if action == "unpay":
        current = db.get_checkin(pid, uid)
        if current and current.get("paid_amount") is not None:
            db.set_paid(pid, uid, None)
            return [checkin_message(poll, f"{current['display_name']} 已取消付款")]
        return [checkin_message(poll, "你還沒有標記付款")]

    if action == "show":
        return [card_message(poll, "目前名單")]

    if action == "ask_delete":
        votes = db.get_votes(pid)
        bubble, alt = delete_confirm_card(poll, headcount(v for v in votes if v["choice"] == "yes"))
        return [flex(bubble, alt)]

    if action == "delete":
        db.delete_poll(pid)
        return [TextMessage(text=f"🗑 {name} 已刪除揪團：{poll['date']} {poll['time']} {poll['venue']}")]

    if action == "cancel":
        return [TextMessage(text="已取消，場次沒有被刪除。")]

    return []


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
