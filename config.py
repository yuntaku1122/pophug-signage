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

# 優先表示設定
# 「優先表示1」〜「優先表示5」に設定した画像（店のロゴ・メニュー一覧など）を、
# 通常の画像をPRIORITY_INTERVAL枚表示するごとにまとめて割り込ませる。
# 優先表示に設定した画像は、通常のローテーションからは除外され、
# 割り込みタイミングでのみ（数字の若い順、優先表示1→2→3→4→5）表示される。
PRIORITY_INTERVAL = 5         # 通常画像を何枚表示するごとに優先表示を割り込ませるか

# 画像の表示方式
#   "stretch" = アスペクト比を無視して画面ぴったりに引き伸ばす（余白・トリミングなし）
#   "contain" = 画像全体が欠けずに収まるよう縮小（余白は下のBG_COLORで塗る）
#   "cover"   = 画面いっぱいに敷き詰め、はみ出た部分はトリミング
# Web設定画面・USB設定ファイル(pophug-settings.txt)の両方から変更できる
# （images/.settings.jsonに保存された値が優先され、この値は未設定時の初期値）。
IMAGE_FIT_MODE = "contain"
BG_COLOR = (0, 0, 0)         # contain/coverモードで余白が出た場合の色

# ワイヤレスアップロードサーバー設定
UPLOAD_ENABLED = True
UPLOAD_PORT = 8080
UPLOAD_HOST = "::"           # IPv4/IPv6両対応（同一Wi-Fi内のiPhone・Macどちらからも見えるように）

# QRコード表示ボタン設定
# ラズパイ単体運用時、IPアドレスが分からずアップロードサーバーにアクセスできない問題への対応。
# 外付けボタンを押すと、アップロードページのURLをQRコードで画面に一時表示する。
QR_BUTTON_ENABLED = True
QR_BUTTON_GPIO_PIN = 17       # BCM番号。配線に合わせて変更（ボタンはこのピンとGNDの間に接続、内部プルアップ使用）
QR_DISPLAY_SECONDS = 30       # QRコードを表示し続ける時間（秒）
NOTICE_DISPLAY_SECONDS = 6    # Web側の操作結果をサイネージ画面にバナー表示する時間（秒）
NOTICE_STICKY_MAX_SECONDS = 600  # 「取り外すまで表示」系の通知の上限（秒）。
                                  # USB取り外し検知で本来は即座に消えるが、
                                  # 検知漏れ時に永久に画面をふさがないための保険の上限
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

# 表示リセット設定
# 次の事業者に本体を引き継ぐ時などに使う。ボタン長押しで、画像データ自体は
# 一切消さずに「表示状態」だけを初期化する（固定表示画像だけが表示され、
# それ以外は非表示になる）。起動直後は固定表示（市のお知らせ等）のみが表示され、
# 次の事業者がUSBメモリーを挿すと、その中身がそのまま表示されるようになる。
RESET_DISPLAY_HOLD_SECONDS = 7       # ボタン長押しで表示をリセットするまでの秒数（WIFI_SETUPより長く、SHUTDOWNより短く設定すること）

# スタンドアロンモード設定
# 外部Wi-Fiが存在しない屋外（公園・催事場・運動公園など）での移動販売を想定。
# 知っているWi-Fiに繋がっていない状態が続くと、自動的に自分専用のアクセスポイントを
# 「常時」立てるモードに切り替わる。Wi-Fiセットアップモードと違い、タイムアウトで
# 自動終了せず、スマホが接続する/しないに関わらず稼働し続ける（画面には出ない裏方の状態で、
# ボタンを押した時だけ接続情報を表示する）。このモード中はインターネットに出られないため、
# アップデート確認は行えない。
# ※以前はこのブロックが誤って2重定義されており、後方の値（20秒/120秒）が常に有効に
#   なっていた（1つ目の値は実際には使われていなかった）。整理のため1つに統合した。
STANDALONE_AUTO_ENABLED = True       # 自動判定を有効にするか
STANDALONE_BOOT_GRACE_SECONDS = 20   # 起動直後、既知のWi-Fiへの接続を試す猶予時間（秒）
STANDALONE_CHECK_INTERVAL = 120      # その後の再判定間隔（秒）※接続が途中で切れた場合の検知用

# シャットダウン設定
# pophugユーザーがパスワード無しで実行できるよう /etc/sudoers.d/pophug-shutdown で
# 個別に許可しておく必要がある（README参照）。
SHUTDOWN_COMMAND = ["sudo", "/sbin/shutdown", "-h", "now"]
SHUTDOWN_HOLD_SECONDS = 10           # QRボタンをこの秒数以上長押しするとシャットダウンする

# アップデート機能設定
# GitHubのReleasesを更新配信先として使う。"ユーザー名/リポジトリ名"の形式。
GITHUB_REPO = "yuntaku1122/pophug-signage"

# USBオフラインアップデート設定
# 出先など、Wi-Fiが無くWeb設定画面のオンラインアップデートが使えない状況でも、
# GitHub Releaseの「Source code (zip)」をそのままUSBメモリーのルートに
# 置くだけで更新できるようにする機能（ファイル名のリネームは不要。
# version.py/main.py/signage_state.pyが単一のトップフォルダに揃っているかで
# 判定するため）。誤って古い/無関係なUSBを挿してしまった時の誤爆を防ぐため、
# 挿しただけでは適用せず「検出・ステージング」だけを行い（pophug-usb-import）、
# 実際の適用は本体ボタンの長押しで明示的に確定してもらう2段階方式にしている。
USB_UPDATE_CONFIRM_HOLD_SECONDS = 8  # 保留中のUSBアップデートを確定するまでの長押し秒数
                                      # （保留中はボタンの意味が丸ごとこれに切り替わるため、
                                      #   他の長押し秒数と衝突しないよう独立した値でよい）
