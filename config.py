# ============================================
# KitchenCar POP Signage - config.py
# ============================================

# 画面設定
# Raspberry Pi + モバイルディスプレイでは FULLSCREEN=True, ROTATE_SCREEN はディスプレイの向きに合わせて調整
SCREEN_WIDTH = 400
SCREEN_HEIGHT = 700
FPS = 60
FULLSCREEN = False       # Pi実機ではTrueにする
ROTATE_SCREEN = False    # 縦置き設置で画面自体が90度回転している場合はTrue

# POP画像設定
IMAGE_FOLDER = "./images"
IMAGE_INTERVAL = 12          # 1枚あたりの表示秒数
TRANSITION_DURATION = 1.0    # クロスフェード時間（秒）
RESCAN_INTERVAL = 5           # 画像フォルダの再スキャン間隔（秒）※iPhoneからのアップロード/表示切替を自動反映

# 画像の表示方式
#   "contain" = 画像全体が欠けずに収まるよう縮小（余白は下のBG_COLORで塗る）
#   "cover"   = 画面いっぱいに敷き詰め、はみ出た部分はトリミング
IMAGE_FIT_MODE = "contain"
BG_COLOR = (0, 0, 0)         # containモード時の余白（黒帯）の色

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
