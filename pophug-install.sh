#!/bin/bash
# ============================================
# pophug-install.sh
#
# まっさらなRaspberry Pi OS（Pi Zero 2W H・Pi 3B等、機種を問わない）に対して、
# pophug-signageの動作に必要なインストール作業を1回で行うブートストラップ
# スクリプト。従来READMEに分散していた手順（venv構築・sudoers設定×3・
# ヘルパースクリプト配置・systemdユニット登録・udevルール登録・CLI起動モードへの
# 切り替え）を1本化した。
#
# 量産用マスターイメージを作らず、中古Pi等を1台ずつ個別にセットアップする
# 運用を想定している（複製前提ではなく、まっさらな状態から素早く立ち上げる
# ための手段）。既にインストール済みの項目は上書き・スキップされるだけなので、
# 一度セットアップ済みの機体に対して再実行しても安全（冪等）。
#
# 前提条件（実行前に確認すること）:
#   ・Raspberry Pi Imagerでの書き込み時、ユーザー名を "pophug" にしておくこと
#     （"pi" 等のままだと、他のスクリプトが前提とする /home/pophug/pophug-signage
#     というパスと一致せず、正しく動作しない）
#   ・ホスト名は初期値 "pophug" のままにしておくこと
#     （pophug-hostname-setupが「複製直後・セットアップ直後の初回起動」を
#     検知する条件のため。ここで独自のホスト名を設定してしまうと、
#     初回起動時のデータ初期化・ホスト名個別化が動作しない）
#   ・インターネット接続が必要（apt/pip/gitのため）。現場のWi-Fiではなく、
#     セットアップ作業を行う場所のWi-Fi等を想定している
#   ・このリポジトリを /home/pophug/pophug-signage に clone してから、
#     その中で実行すること
#
# 使い方:
#   git clone https://github.com/yuntaku1122/pophug-signage.git
#   cd pophug-signage
#   bash pophug-install.sh
#   sudo reboot
#
# ご利用にあたっての注意（実体験から）:
#   ・テザリング回線やPi Zero系の無線は不安定になりやすく、apt-get/pip install
#     の途中でSSH接続そのものが切れることがある（このスクリプトはscreen経由で
#     自動的に自分自身を再実行することでこれに対処しているので、通常は
#     気にする必要は無い。詳細は下記の「SSH切断への耐性」を参照）
#   ・OSやPythonのバージョンによっては、pygame等の事前ビルド済みパッケージ
#     （wheel）が無く、ソースからのビルドが発生することがある。この場合、
#     特にPi Zero系では数十分単位の時間がかかることがあるが、異常ではない
# ============================================

set -e

# ---- SSH切断への耐性: screenセッション内での自動再実行 ----
# pip install等、時間のかかる処理の途中でSSH接続が切れると、それまでの
# 処理も道連れで中断されてしまう（テザリング回線・Pi Zero系の無線が不安定な
# 環境で実際に発生した）。既にscreen/tmuxの中で動いている場合を除き、
# 自動的にscreenセッションの中で自分自身を再実行する。SSHが繋がっている間は
# 普段と全く同じ見た目で進み、途中でSSHが切れても処理自体は裏側で継続される
# （screenはSIGHUPを受けても自動的にデタッチして生き残る仕様のため）。
# 再接続後は `screen -r pophug-install` で状況を確認・復帰できる。
if [ -z "${STY:-}" ] && [ -z "${TMUX:-}" ] && [ -z "${POPHUG_INSTALL_NO_SCREEN:-}" ]; then
    if ! command -v screen > /dev/null 2>&1; then
        echo "SSH切断への耐性のため、screenをインストールします..."
        sudo apt-get update -qq
        sudo apt-get install -y screen
    fi
    echo "screenセッション（pophug-install）内で実行します。"
    echo "SSHが途中で切れても処理は継続されます。再接続後は次で状況を確認できます:"
    echo "  screen -r pophug-install"
    echo ""
    export POPHUG_INSTALL_NO_SCREEN=1
    # ここは意図的に exec ではなく通常の呼び出しにしている。exec だとscreen
    # コマンド自体に処理を完全に明け渡してしまい、環境によってscreenが
    # 何らかの理由で即座に終了してしまった場合（実機で実際に発生）、
    # インストール処理が一切実行されないまま、エラーも出さず終わってしまう。
    # 終了コードを見て、screen経由が失敗した場合はscreen無しで直接続行する
    # （POPHUG_INSTALL_NO_SCREEN=1は既にexport済みなので、下にそのまま
    # 処理が続けば二重にscreenへ入ろうとすることはない）。
    if screen -S pophug-install bash "$0" "$@"; then
        exit 0
    else
        echo ""
        echo "⚠ screen経由での実行がうまくいかなかったため、screen無しで直接"
        echo "  続行します（この環境ではSSH切断への耐性が効かない状態です。"
        echo "  原因不明な場合は開発者に状況を報告してください）"
        echo ""
    fi
fi

EXPECTED_DIR="/home/pophug/pophug-signage"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="$(whoami)"

echo "=== pophug インストーラー ==="
echo "実行ユーザー : $RUN_USER"
echo "実行パス     : $APP_DIR"
echo ""

# ---- 前提条件のチェック ----
if [ "$RUN_USER" != "pophug" ]; then
    echo "⚠ 実行ユーザーが 'pophug' ではありません（現在: $RUN_USER）。"
    echo "  systemdユニット・sudoers設定は全て 'pophug' ユーザー前提で書かれているため、"
    echo "  このまま続けると正しく動作しません。"
    echo "  Raspberry Pi Imagerの「カスタマイズ」でユーザー名を pophug にして"
    echo "  書き込み直すことを強く推奨します。"
    read -p "  それでも続行しますか？ (yes と入力): " CONFIRM_USER
    if [ "$CONFIRM_USER" != "yes" ]; then
        echo "中止しました"
        exit 1
    fi
fi

if [ "$APP_DIR" != "$EXPECTED_DIR" ]; then
    echo "⚠ 実行パスが想定と異なります。"
    echo "  想定 : $EXPECTED_DIR"
    echo "  実際 : $APP_DIR"
    echo "  pophug-hostname-setup・pophug-usb-importはこのパスを直接埋め込んで"
    echo "  参照するため、ここ以外の場所では正しく動作しません。"
    read -p "  それでも続行しますか？ (yes と入力): " CONFIRM_PATH
    if [ "$CONFIRM_PATH" != "yes" ]; then
        echo "中止しました。$EXPECTED_DIR に clone し直してから再実行してください。"
        exit 1
    fi
fi

echo ""
echo "--- 1/10: OSパッケージのインストール ---"
sudo apt-get update
# fonts-noto-cjk: 日本語フォント。main.pyのget_japanese_fontが
# /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc を候補として
# 探しにいくが、これが無いとpygameのデフォルトフォント（日本語グリフを
# 含まない）にフォールバックし、画面の文字が全て豆腐（□）になる。
# 以前から稼働している機体で問題が起きなかったのは、そのイメージに
# たまたま日本語フォントが入っていた（Desktop版由来等）偶然によるもので、
# 新規セットアップでは確実に入れておく必要がある（pygame/venvの件と同種の問題）。
sudo apt-get install -y python3-venv python3-pip git exfatprogs fonts-noto-cjk

echo ""
echo "--- 2/10: Python仮想環境の構築 ---"
# --system-site-packages 付きで作る。pygame・gpiozeroは、Raspberry Pi OSの
# aptパッケージ版（python3-pygame等）がハードウェア（kmsdrmでの画面描画、
# GPIOアクセス）向けに最適化されているのに対し、pipが取得するpygameは
# 汎用ビルドの（やや古い）SDLを内蔵しており、新しいカーネルのDRM実装との
# 組み合わせで「kmsdrm not available」となる相性問題が実機
# （Raspberry Pi Zero 2 W、カーネル6.18系）で確認されている。
# --system-site-packages を付けると、requirements.txt内のバージョン指定の
# 無いpygame/gpiozeroは「システム版で既に満たされている」としてpipが
# 再インストールをスキップするようになる（flask・qrcode等システムに
# 無いものは、これまで通りpipでvenv内にインストールされる）。
if [ -d "$APP_DIR/venv" ] && [ -f "$APP_DIR/venv/pyvenv.cfg" ] \
        && grep -q "include-system-site-packages = false" "$APP_DIR/venv/pyvenv.cfg"; then
    echo "  既存のvenvが --system-site-packages 無しで作られているため作り直します"
    echo "  （pygameがシステム版ではなくpip版になり、上記のkmsdrm関連の不具合を"
    echo "  引き起こすため。既存のvenv内に手動で追加したパッケージがあれば"
    echo "  失われるので注意）"
    rm -rf "$APP_DIR/venv"
fi
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv --system-site-packages "$APP_DIR/venv"
    echo "  venvを新規作成しました（--system-site-packages 付き）"
else
    echo "  venvは既に --system-site-packages 付きで存在するため作成をスキップしました"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo ""
echo "--- 3/10: ヘルパースクリプトの配置（root所有・実行専用） ---"
for script in pophug-netctl pophug-update-apply pophug-usb-import pophug-hostname-setup; do
    if [ ! -f "$APP_DIR/$script" ]; then
        echo "  ⚠ $script が見つからないためスキップします"
        continue
    fi
    sudo cp "$APP_DIR/$script" /usr/local/bin/
    sudo chown root:root /usr/local/bin/"$script"
    sudo chmod 755 /usr/local/bin/"$script"
    echo "  配置: /usr/local/bin/$script"
done

echo ""
echo "--- 4/10: sudoers設定（各ヘルパー単体だけをパスワード無しで許可） ---"
SHUTDOWN_PATH="$(command -v shutdown)"
echo "pophug ALL=(ALL) NOPASSWD: $SHUTDOWN_PATH -h now" | sudo tee /etc/sudoers.d/pophug-shutdown > /dev/null
sudo chmod 440 /etc/sudoers.d/pophug-shutdown

echo "pophug ALL=(ALL) NOPASSWD: /usr/local/bin/pophug-netctl *" | sudo tee /etc/sudoers.d/pophug-netctl > /dev/null
sudo chmod 440 /etc/sudoers.d/pophug-netctl

echo "pophug ALL=(ALL) NOPASSWD: /usr/local/bin/pophug-update-apply *" | sudo tee /etc/sudoers.d/pophug-update-apply > /dev/null
sudo chmod 440 /etc/sudoers.d/pophug-update-apply

# USB書き出し機能の「PCから今すぐ書き出す」ボタン（Web設定画面）専用。
# 引数を固定文字列（manual-export）だけに絞り、ワイルドカードは使わない
# （他のヘルパーと違い、この一機能にしか使わない単一の呼び出しのため、
# 可能な限り許可範囲を狭くしている）。
echo "pophug ALL=(ALL) NOPASSWD: /usr/local/bin/pophug-usb-import manual-export" | sudo tee /etc/sudoers.d/pophug-usb-import-manual-export > /dev/null
sudo chmod 440 /etc/sudoers.d/pophug-usb-import-manual-export

sudo visudo -c
echo "  sudoers設定OK（visudo -c で構文確認済み）"
echo "  ※ upload_server.pyのSHUTDOWN_COMMANDが上記のシャットダウンパスと"
echo "    一致しているか、念のため確認しておくこと（$SHUTDOWN_PATH）"

echo ""
echo "--- 5/10: systemdユニットの配置 ---"
sudo cp "$APP_DIR/pophug-signage.service" /etc/systemd/system/
sudo cp "$APP_DIR/pophug-hostname-setup.service" /etc/systemd/system/

# USBインポート用のテンプレートユニットは、systemd上は "@" を含むファイル名で
# 配置する必要がある（pophug-usb-import@.service）。リポジトリ内での実際の
# ファイル名が違う場合は、この1行だけ実際のファイル名に合わせて修正すること。
USB_IMPORT_SERVICE_SRC="$APP_DIR/pophug-usb-import@.service"
if [ ! -f "$USB_IMPORT_SERVICE_SRC" ]; then
    USB_IMPORT_SERVICE_SRC="$APP_DIR/pophug-usb-import_.service"
fi
if [ -f "$USB_IMPORT_SERVICE_SRC" ]; then
    sudo cp "$USB_IMPORT_SERVICE_SRC" /etc/systemd/system/pophug-usb-import@.service
else
    echo "  ⚠ pophug-usb-import@.service が見つかりませんでした。USBメモリー機能は動作しません。"
fi

echo ""
echo "--- 6/10: USBメモリー自動検出（udevルール）の設定 ---"
sudo cp "$APP_DIR/99-pophug-usb.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules

echo ""
echo "--- 7/10: サービスの有効化・GPIOアクセス権限の確認 ---"
sudo systemctl daemon-reload
sudo systemctl enable pophug-hostname-setup.service
sudo systemctl enable pophug-signage.service
# pophug-usb-import@ はudevイベントから都度起動されるテンプレートユニットのため、
# 常時有効化(enable)は不要（daemon-reloadだけで認識される）

# GPIOボタン(gpiozero)・画面出力(kmsdrm)には、pophugユーザーが
# gpio/video/render等のグループに所属している必要がある。Raspberry Pi Imagerで
# ユーザーを作成した場合は通常自動で付与されるが、念のため明示的に確認・付与する
# （既に所属していれば何も変わらない、安全な操作）
for grp in gpio video render input dialout plugdev; do
    if getent group "$grp" > /dev/null 2>&1; then
        sudo usermod -aG "$grp" pophug
    fi
done

echo ""
echo "--- 8/10: 起動モードをCLI（コンソール）に設定 ---"
# pophug-signageの画面描画はpygame+kmsdrmで、デスクトップ環境（X11等）を
# 介さず直接画面（/dev/dri）を掴む方式のため、デスクトップ環境は本来不要。
# CLIモード（multi-user.target）にしておくことで、デスクトップ環境の
# 起動処理が丸ごと省かれ、起動が速くなりメモリ消費も減る
# （Pi Zero系では特に効果がある）。また、デスクトップ環境が同時に起動して
# いると、まれにkmsdrmとの画面（/dev/dri）の奪い合いが起きることがあるため、
# その回避にもなる。
# Desktop版のRaspberry Pi OSで書き込んでしまった場合でも、ここで自動的に
# CLIモードへ切り替える（Lite版で最初からCLIモードの場合は何もしない）。
CURRENT_BOOT_TARGET="$(systemctl get-default 2>/dev/null || echo "unknown")"
if [ "$CURRENT_BOOT_TARGET" = "multi-user.target" ]; then
    echo "  既にCLIモード（multi-user.target）です。変更はありません"
elif [ "$CURRENT_BOOT_TARGET" = "graphical.target" ]; then
    sudo systemctl set-default multi-user.target
    echo "  デスクトップモード（graphical.target）からCLIモード（multi-user.target）に変更しました"
    echo "  （元に戻したい場合: sudo systemctl set-default graphical.target）"
else
    echo "  ⚠ 現在の起動モード（$CURRENT_BOOT_TARGET）を認識できなかったため、変更をスキップしました"
    echo "    手動で切り替えたい場合: sudo systemctl set-default multi-user.target"
fi

echo ""
echo "--- 9/10: 画面解像度の明示的な固定（video=パラメータ） ---"
# 【2026-09の実機トラブルから追加】cmdline.txtにvideo=パラメータが無いと、
# カーネル/DRMドライバは接続したモニターのEDID情報から解像度を自動決定する。
# ある実機で、たまたま接続したモバイルディスプレイのpreferredモードが
# 1920x1080になり、その時点で動作確認済みだった1280x720（720p）の2.25倍の
# ピクセル数で動いてしまい、Pi Zero 2W（RAM 512MB）のメモリを圧迫してOOM
# Killerによる強制再起動ループを引き起こした実例がある。
#
# 【2026-09の設計変更で解消】原因は「全画像を常駐メモリーに保持していたこと」
# であり、解像度そのものが問題だったわけではなかった。main.py側の画像
# キャッシュを「固定表示の画像だけ常駐、それ以外は表示中+数枚先までの
# ウィンドウだけ保持」する方式に変更したことで、常駐メモリー使用量が
# 画像の総枚数ではなく「固定表示枚数（MAX_PINNED_IMAGES）＋先読み枚数
# （IMAGE_PREFETCH_WINDOW）」でほぼ頭打ちになるようになったため、1080pでの
# 運用も安全に行えるようになった。そのため、このスクリプトの既定値は
# 1920x1080（フルHD）としている。
#
# 接続するモニターの型番・向きによって適切な値は変わり得るため、
# 環境変数 POPHUG_VIDEO_MODE で上書きできるようにしてある
# （例: POPHUG_VIDEO_MODE="HDMI-A-1:1280x720@60" bash pophug-install.sh ）。
# 既にvideo=が（値を問わず）設定済みの場合は、意図的な設定を上書きしないよう
# 何もしない（このスクリプトの「既存設定は上書きしない」という冪等性の方針に
# 合わせている）。
POPHUG_VIDEO_MODE="${POPHUG_VIDEO_MODE:-HDMI-A-1:1920x1080@60}"

CMDLINE_PATH=""
if [ -f /boot/firmware/cmdline.txt ]; then
    CMDLINE_PATH="/boot/firmware/cmdline.txt"
elif [ -f /boot/cmdline.txt ]; then
    CMDLINE_PATH="/boot/cmdline.txt"
fi

if [ -z "$CMDLINE_PATH" ]; then
    echo "  ⚠ cmdline.txtが見つからなかったため、解像度の固定をスキップしました"
    echo "    手動で設定したい場合、/boot/firmware/cmdline.txt（または/boot/cmdline.txt）の"
    echo "    末尾に半角スペース区切りで video=${POPHUG_VIDEO_MODE} を追記してください"
elif grep -q "video=" "$CMDLINE_PATH"; then
    echo "  既にvideo=パラメータが設定済みのため変更しませんでした（$CMDLINE_PATH）"
    echo "  現在の内容: $(cat "$CMDLINE_PATH")"
else
    sudo cp "$CMDLINE_PATH" "${CMDLINE_PATH}.pophug-install.bak"
    sudo sed -i "s/\$/ video=${POPHUG_VIDEO_MODE}/" "$CMDLINE_PATH"
    echo "  video=${POPHUG_VIDEO_MODE} を追記しました（$CMDLINE_PATH）"
    echo "  バックアップ: ${CMDLINE_PATH}.pophug-install.bak"
    echo "  ※ 実際にこの解像度で起動しているかは、再起動後に以下で確認できます:"
    echo "    SDL_VIDEODRIVER=kmsdrm python3 -c \"import pygame; pygame.display.init(); print(pygame.display.Info())\""
fi

echo ""
echo "--- 10/10: 不要なデスクトップ向け音声サービスの無効化 ---"
# 【2026-09の実機トラブルから追加】pophug-signageは画面描画にpygame+kmsdrmを
# 直接使い、音声は一切再生しない（pygame.mixerも未使用）。にもかかわらず、
# Raspberry Pi OSのイメージにはデスクトップ向けの音声セッション管理一式
# （pipewire, pipewire-pulse, wireplumber, mpris-proxy等）がユーザーセッションの
# デフォルトサービスとして含まれており、ヘッドレス運用でも自動起動してしまう。
# これらは常時数十MBのメモリと若干のCPUを消費し続け（存在しないALSAデバイスへの
# 初期化・recover処理を延々と繰り返すこともある）、Pi Zero 2W（RAM 512MB）では
# 無視できない負荷になる。今回の根本原因ではなかったが、メモリに余裕を持たせる
# 対策として、新規セットアップ時にまとめて無効化しておく。
#
# systemctl --user はユーザーセッションが必要なため、SSH等の対話セッション無しで
# 実行されるとエラーになることがある。その場合でも他のインストール手順を
# 止めないよう、失敗は無視する（|| true）。
DESKTOP_AUDIO_UNITS="pipewire.socket pipewire-pulse.socket pipewire.service pipewire-pulse.service wireplumber.service mpris-proxy.service filter-chain.service xdg-permission-store.service"
DISABLED_ANY=0
for unit in $DESKTOP_AUDIO_UNITS; do
    if systemctl --user disable --now "$unit" > /dev/null 2>&1; then
        DISABLED_ANY=1
    fi
    systemctl --user mask "$unit" > /dev/null 2>&1 || true
done
if [ "$DISABLED_ANY" = "1" ]; then
    echo "  デスクトップ向け音声サービス（pipewire等）を無効化しました"
else
    echo "  対象の音声サービスは見つからなかった、または既に無効化済みでした"
fi
echo "  （元に戻したい場合: systemctl --user unmask <サービス名> の後、"
echo "    systemctl --user enable --now <サービス名>）"

# ---- 再起動後の案内メッセージの組み立て ----
# pophug-hostname-setupは「ホスト名が初期値 'pophug' のままかどうか」だけで
# 動作を判定する。この個体が実際にどちらの状態かをここで確認し、
# 案内メッセージの内容を出し分ける（初期化済みの機体に対して「初期化されます」
# という警告を出すと、実際には何も起きないのに紛らわしいため）。
CURRENT_HOSTNAME="$(hostname)"

# 再起動後の新しいホスト名は、pophug-hostname-setup と全く同じロジック
# （wlan0のMACアドレス下4桁を大文字化して付与）で、再起動前でも計算できる。
FUTURE_HOSTNAME=""
if [ -r /sys/class/net/wlan0/address ]; then
    MAC="$(cat /sys/class/net/wlan0/address 2>/dev/null || true)"
    if [ -n "$MAC" ]; then
        SUFFIX="$(echo "$MAC" | tr -d ':' | tr '[:lower:]' '[:upper:]' | tail -c 5)"
        FUTURE_HOSTNAME="pophug-${SUFFIX}"
    fi
fi

echo ""
echo "=== インストール完了 ==="
echo ""
echo "この後、以下を実行して再起動してください:"
echo "  sudo reboot"
echo ""

if [ "$CURRENT_HOSTNAME" = "pophug" ]; then
    echo "現在ホスト名が初期値 'pophug' のままのため、再起動時にpophug-hostname-setupが"
    echo "自動的に動作し、以下が行われます（複製直後・セットアップ直後の初回起動として"
    echo "扱われるため）:"
    echo "・ホスト名の個別化"
    if [ -n "$FUTURE_HOSTNAME" ]; then
        echo "    → 再起動後の新しいホスト名は次になる見込みです: $FUTURE_HOSTNAME"
        echo "      （wlan0のMACアドレスから機械的に決まる値。念のため実際の値は"
        echo "      起動後に確認すること）"
    else
        echo "    → wlan0が見つからず、新しいホスト名を事前に計算できませんでした"
        echo "      （起動後、本体画面またはルーターの接続機器一覧で確認してください）"
    fi
    echo "・保存済みWi-Fi情報の初期化（今設定した接続用Wi-Fiの情報も消えます）"
    echo "・images/フォルダの初期化"
    echo "・ハードウェアウォッチドッグの有効化（設定変更を伴う場合は自動でもう一度再起動）"
    echo ""
    echo "⚠ ホスト名が変わるとSSHの接続先も変わります。再起動後に"
    echo "  ssh pophug@pophug.local で繋がらない場合は、上記の新しいホスト名"
    if [ -n "$FUTURE_HOSTNAME" ]; then
        echo "  （$FUTURE_HOSTNAME）で以下のように接続し直してください:"
        echo "    ssh pophug@${FUTURE_HOSTNAME}.local"
    else
        echo "  で ssh pophug@<新しいホスト名>.local として接続し直してください。"
    fi
else
    echo "現在のホスト名（$CURRENT_HOSTNAME）はすでに初期値 'pophug' から変更済みのため、"
    echo "pophug-hostname-setupの初期化処理（ホスト名変更・保存済みWi-Fi情報や"
    echo "images/フォルダの初期化）は実行されません。"
    echo ""
    echo "→ 再起動してもSSHの接続先（$CURRENT_HOSTNAME）は変わらず、写真や"
    echo "  Wi-Fi設定もそのまま残ります。安心して再起動してください。"
fi
