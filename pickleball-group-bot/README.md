# 🏓 匹克球揪團群組小幫手

把機器人邀進 LINE 群組後，團主輸入「揪團」訊息，機器人就會建立一張報名卡片，群組成員按按鈕投票「參加／不參加」，名單自動更新。

## 使用方式

**團主建立揪團**：在群組輸入（每行一個欄位，冒號可用全形或半形，也可以寫成一行）

```
揪團
日期：10/5（日）
時間：19:00-21:00
場地：信義運動中心 3F
價錢：150 元/人
人數：8
```

| 欄位 | 必填 | 可接受的寫法 |
|---|---|---|
| 日期 | ✅ | 日期 |
| 時間 | ✅ | 時間 |
| 場地 | ✅ | 場地、地點、球場 |
| 價錢 | ✅ | 價錢、價格、費用、場地費、金額 |
| 人數 | 選填 | 人數、名額、上限、人數上限 |

- 只輸入「揪團」→ 機器人回覆格式範本
- 缺少必填欄位 → 機器人提醒缺了哪幾項
- 群組內其他聊天（包含「揪團嗎？」這類閒聊）→ 機器人不回應

**成員投票**：按卡片上的「✋ 參加」或「🙅 不參加」，機器人會回覆一張最新名單卡片（卡片上也有按鈕，可以繼續投票）。

- 可以隨時改票，以最後一次為準
- 有設定人數時，額滿後報名的人會排入**候補**；有人改成不參加，候補會**依報名順序自動遞補**
- 重複按「參加」不會影響原本的順位；改成不參加再改回參加，則會排到最後

## 檔案說明

| 檔案 | 用途 |
|---|---|
| `app.py` | 主程式：接收 LINE 訊息、按鈕投票 |
| `polls.py` | 解析揪團訊息、計算正取／候補名單（要改欄位名稱或範本在這裡） |
| `cards.py` | 報名卡片的外觀（Flex Message） |
| `db.py` | 資料庫（PostgreSQL；沒設定時用本機 SQLite 測試） |

---

## 設定步驟

### 1. 建立 LINE Channel

與上一個客服機器人相同：到 [LINE Developers Console](https://developers.line.biz/console/) 建立 Messaging API Channel，取得 **Channel secret** 與 **Channel access token**。
（建議新開一個官方帳號給群組小幫手用，不要和客服機器人共用。）

### 2. 允許機器人加入群組 ⚠️ 必做

- LINE Developers Console → **Messaging API** 分頁 → **Allow bot to join group chats** → 按 Edit 改成**啟用**
  （或在 LINE Official Account Manager → 設定 → 帳號設定 → 「加入群組或多人聊天室」選**接受邀請**）
- 同一處的「回應設定」：**Webhook 開啟、自動回應訊息關閉**

### 3. 建立免費資料庫（Neon）

1. 到 <https://neon.tech> 註冊（可用 GitHub 或 Google 帳號）。
2. 建立 Project，Region 選 **AWS Asia Pacific (Singapore)**，離台灣最近。
3. 建立後在 Dashboard 按 **Connect**，複製連線字串，長得像：
   `postgresql://user:密碼@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`

資料表會在機器人第一次啟動時自動建立，不需要手動操作。
（也可以用 Supabase：Project Settings → Database → Connection string，選 *Session pooler* 的 URI。）

### 4. 上傳 GitHub → 部署 Render

1. 把資料夾內所有檔案上傳到新的 GitHub repository。
2. Render → **New → Blueprint** 選擇該 repository。
3. 填入三個環境變數：
   - `LINE_CHANNEL_SECRET`
   - `LINE_CHANNEL_ACCESS_TOKEN`
   - `DATABASE_URL`（步驟 3 的連線字串）
4. 部署完成後打開網址，看到 `Pickleball group bot is running.` 即成功。

### 5. 設定 Webhook 並加入群組

1. LINE Developers Console → Webhook URL 填 `https://你的網址.onrender.com/callback` → **Verify** → 開啟 **Use webhook**。
2. 用手機掃 QR code 加機器人好友，再到球友群組 → 邀請 → 選機器人。
3. 機器人進群後會自動發送使用說明，接著輸入「揪團」測試。

---

## 常見問題

| 狀況 | 解法 |
|---|---|
| 邀請不到群組 | 步驟 2「允許加入群組」沒開 |
| 按按鈕沒反應 | Render 免費方案休眠中，第一次要等 30 秒～1 分鐘；可到 Render **Logs** 查看錯誤 |
| 名單上顯示「成員」 | 少數情況 LINE 無法取得名稱（例如對方已退出群組），不影響投票 |
| 投票資料不見 | 確認 `DATABASE_URL` 有填；沒填的話會存在 Render 本機，重啟就會清空 |

> Neon 免費方案閒置時資料庫會自動暫停，下次使用時幾秒內會自動喚醒，資料不會遺失。

## 之後可以擴充

- 團主「結束報名」或「取消揪團」
- 輸入「名單」查看最近一次揪團的名單
- 活動前一天自動提醒參加者
- 攜伴報名（+1）、自動計算每人分攤費用
