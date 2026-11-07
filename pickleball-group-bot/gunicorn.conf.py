"""gunicorn 設定檔。

Render 執行 `gunicorn app:app` 時，gunicorn 會自動讀取同一個資料夾裡的
gunicorn.conf.py，所以不需要修改 Render 的 Start Command。
"""

# 同時處理多個請求：2 個 worker × 每個 4 條執行緒 = 最多同時處理 8 個事件。
# 機器人大部分時間在等 Neon 和 LINE 回應，用執行緒就能同時服務多人，
# 不會像只有 1 個 worker 時，大家同時按「參加」要排隊、排到 LINE 放棄。
workers = 2
threads = 4

# 先在主程序載入 app.py 再分出 worker：
# 資料表建立與欄位升級只會執行一次，避免兩個 worker 同時建表而出錯。
preload_app = True

# Neon 從暫停中喚醒時，單一請求可能需要多等幾秒。
timeout = 60
