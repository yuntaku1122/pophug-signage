# ============================================
# KitchenCar POP Signage - config.py
# ============================================

# 画面設定
# Raspberry Pi + モバイルディスプレイでは FULLSCREEN=True, ROTATE_SCREEN はディスプレイの向きに合わせて調整
SCREEN_WIDTH = 400
SCREEN_HEIGHT = 700
FPS = 60
FULLSCREEN = True        # Pi実機ではTrue。Macでウィンドウ表示させたい時だけFalseにする

# 画面回転（縦置き設置用）
# 0   = 回転なし（横のまま）
# 90  = 時計回りに90度回転（画面右側が上になる向きで物理設置している場合）
# 180 = 180度回転
# 270 = 反時計回りに90度回転（画面左側が上になる向きで物理設置している場合）
# 実機に取り付けてから、上下が正しくなる値を探して設定してください
ROTATE_SCREEN = 90

# POP画像設定
IMAGE_FOLDER = "./images"
IMAGE_INTERVAL = 12          # 1枚あたりの表示秒数
TRANSITION_DURATION = 0.5    # クロスフェード時間（秒）
TRANSITION_TYPE = "fade"     # 切り替え効果: fade / slide_left / slide_right / slide_up / slide_down
RESCAN_INTERVAL = 5           # 画像フォルダの再スキャン間隔（秒）※新規アップロードの検知用
HIDDEN_CHECK_INTERVAL = 1     # 表示/非表示の切替を検知する間隔（秒）※軽い処理なので短くしてある

# 画像の表示方式
#   "stretch" = アスペクト比を無視して画面ぴったりに引き伸ばす（余白・トリミングなし）
#   "contain" = 画像全体が欠けずに収まるよう縮小（余白は下のBG_COLORで塗る）
#   "cover"   = 画面いっぱいに敷き詰め、はみ出た部分はトリミング
IMAGE_FIT_MODE = "stretch"
BG_COLOR = (0, 0, 0)         # contain/coverモードで余白が出た場合の色

# ワイヤレスアップロードサーバー設定
UPLOAD_ENABLED = True
UPLOAD_PORT = 8080
UPLOAD_HOST = "0.0.0.0"      # 同一Wi-Fi内のiPhoneから見えるように

# QRコード表示ボタン設定
# ラズパイ単体運用時、IPアドレスが分からずアップロードサーバーにアクセスできない問題への対応。
# 外付けボタンを押すと、アップロードページのURLをQRコードで画面に一時表示する。
QR_BUTTON_ENABLED = True
QR_BUTTON_GPIO_PIN = 17       # BCM番号。配線に合わせて変更（ボタンはこのピンとGNDの間に接続、内部プルアップ使用）
QR_DISPLAY_SECONDS = 30       # QRコードを表示し続ける時間（秒）
UPLOAD_URL_OVERRIDE = None    # 固定IPで運用する場合など、URLを手動指定したい時は文字列で指定 (例: "http://192.168.4.1:8080")

# 取扱説明モード設定
# images/フォルダに表示できる写真が1枚も無い時（購入直後の無垢な状態）は、
# ここに書かれた内容を自動的にループ表示する（説明書レス運用のため）。
# ボタンの2〜5秒長押しでもいつでも呼び出せる。内容は manual.py で管理している。
MANUAL_HOLD_SECONDS = 2       # ボタン長押しで取扱説明を表示するまでの秒数
MANUAL_PAGE_SECONDS = 8       # 取扱説明1ページあたりの表示秒数

# Wi-Fiセットアップモード設定
# QRボタンを長押しすると、Piが一時的な無垢な状態からでもスマホだけで
# Wi-Fi設定ができるよう、自分専用のアクセスポイントを一時的に立てる。
WIFI_SETUP_HOLD_SECONDS = 5          # ボタン長押しでセットアップモードに入るまでの秒数
WIFI_SETUP_SSID_PREFIX = "pophug-setup"     # 実際のSSIDは末尾にMACアドレス由来の4桁が付く（機体ごとに一意）
WIFI_SETUP_DEFAULT_PASSWORD = "pophugsetup1234"  # 出荷時デフォルト。取扱説明書に記載する想定
WIFI_SETUP_TIMEOUT_SECONDS = 600     # セットアップモードのまま操作が無かった場合に自動キャンセルするまでの秒数

# シャットダウン設定
# pophugユーザーがパスワード無しで実行できるよう /etc/sudoers.d/pophug-shutdown で
# 個別に許可しておく必要がある（README参照）。
SHUTDOWN_COMMAND = ["sudo", "/sbin/shutdown", "-h", "now"]
SHUTDOWN_HOLD_SECONDS = 10           # QRボタンをこの秒数以上長押しするとシャットダウンする

# アップデート機能設定
# GitHubのReleasesを更新配信先として使う。"ユーザー名/リポジトリ名"の形式。
GITHUB_REPO = "yuntaku1122/pophug-signage"
