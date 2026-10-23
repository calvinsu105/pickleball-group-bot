"""產生 LINE Flex Message 卡片（純 dict，方便測試）。"""
from polls import split_roster

GREEN = "#1B7F5B"
GREY = "#888888"


def _row(label, value):
    return {
        "type": "box", "layout": "baseline", "spacing": "md",
        "contents": [
            {"type": "text", "text": label, "color": GREY, "size": "sm", "flex": 2},
            {"type": "text", "text": str(value), "size": "sm", "flex": 6, "wrap": True},
        ],
    }


def _section(title, names, empty_text, color="#111111"):
    body = "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1)) if names else empty_text
    return [
        {"type": "text", "text": title, "weight": "bold", "size": "sm", "color": color, "margin": "lg"},
        {"type": "text", "text": body, "size": "sm", "wrap": True, "margin": "sm",
         "color": "#333333" if names else GREY},
    ]


def _button(label, data, display, style):
    return {
        "type": "button", "style": style, "height": "sm",
        "color": GREEN if style == "primary" else None,
        "action": {"type": "postback", "label": label, "data": data, "displayText": display},
    }


def poll_card(poll, votes, note=None):
    """揪團卡片：活動資訊 + 目前名單 + 投票按鈕。建立時與每次投票後都用同一張卡。"""
    cap = poll["capacity"]
    going, waitlist, not_going = split_roster(votes, cap)
    name = lambda v: v["display_name"] or "成員"

    count = f"{len(going)}/{cap}" if cap else f"{len(going)}"
    info = [
        _row("日期", poll["date"]),
        _row("時間", poll["time"]),
        _row("場地", poll["venue"]),
        _row("價錢", poll["price"]),
    ]
    if cap:
        info.append(_row("人數", f"上限 {cap} 人"))
    if poll.get("creator_name"):
        info.append(_row("團主", poll["creator_name"]))

    roster = _section(f"✋ 參加（{count}）", [name(v) for v in going], "還沒有人報名，快來搶頭香！", GREEN)
    if cap:
        roster += _section(f"⏳ 候補（{len(waitlist)}）", [name(v) for v in waitlist], "無")
    roster += _section(f"🙅 不參加（{len(not_going)}）", [name(v) for v in not_going], "無", GREY)

    pid = poll["id"]
    footer = [
        _button("✋ 參加", f"action=vote&poll={pid}&choice=yes", "✋ 我要參加", "primary"),
        _button("🙅 不參加", f"action=vote&poll={pid}&choice=no", "🙅 我不參加", "secondary"),
    ]
    for b in footer:
        if b["color"] is None:
            del b["color"]

    header_texts = [{"type": "text", "text": "🏓 匹克球揪團", "color": "#FFFFFF", "weight": "bold", "size": "lg"}]
    if note:
        header_texts.append({"type": "text", "text": note, "color": "#DFF5EA", "size": "xs", "wrap": True})

    bubble = {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": GREEN,
                   "paddingAll": "16px", "contents": header_texts},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm",
                 "contents": info + [{"type": "separator", "margin": "lg"}] + roster},
        "footer": {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": footer},
    }
    alt = f"🏓 匹克球揪團 {poll['date']} {poll['time']} @{poll['venue']}（參加 {count}）"
    return bubble, alt[:400]
