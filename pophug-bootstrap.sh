#!/bin/bash
# ============================================
# pophug-bootstrap.sh
#
# 個別インストールの最初の1本。まだリポジトリを持っていない、まっさらな
# Raspberry Pi OS上で最初に実行するスクリプト。これ単体をダウンロードして
# 実行するだけで、リポジトリのclone〜pophug-install.shの実行〜再起動までを
# 一気に行う。
#
# pophug-install.sh自体はリポジトリの中にあるファイルなので、自分自身を
# cloneする処理を自分の中には書けない。そのため、clone処理だけを担う
# 入り口としてこのスクリプトを別に用意している
# （pophug-install.shは、既にcloneしたリポジトリの中から単体で実行する
# こともできる。git pull後の再インストール等はそちらを直接使えばよい）。
#
# 前提条件はpophug-install.shと同じ（README参照）:
#   ・Raspberry Pi Imager書き込み時、ユーザー名を"pophug"に、ホスト名は
#     初期値"pophug"のままにしておくこと
#   ・SSHを有効化しておくこと
#   ・セットアップ用にWi-Fi（インターネット接続）に繋いでおくこと
#     （現場のWi-Fiとは別。現場のWi-FiはWi-Fiセットアップモードで後から設定する）
#
# 使い方（2行。curlでこのファイルだけ先にダウンロードしてから実行する。
# 「curl | bash」のように直接パイプ実行すると、この後・pophug-install.sh内の
# 確認プロンプトに答えられなくなる（標準入力がcurl側に使われてしまう）ため、
# あえてダウンロード→実行の2段階にしている）:
#
#   curl -fsSL -o pophug-bootstrap.sh https://raw.githubusercontent.com/yuntaku1122/pophug-signage/main/pophug-bootstrap.sh
#   bash pophug-bootstrap.sh
# ============================================

set -e

REPO_URL="https://github.com/yuntaku1122/pophug-signage.git"
TARGET_DIR="/home/pophug/pophug-signage"
RUN_USER="$(whoami)"

echo "=== pophug ブートストラップ ==="
echo "実行ユーザー: $RUN_USER"
echo "clone先     : $TARGET_DIR"
echo ""

if [ "$RUN_USER" != "pophug" ]; then
    echo "⚠ 実行ユーザーが 'pophug' ではありません（現在: $RUN_USER）。"
    echo "  clone先（$TARGET_DIR）に書き込めない可能性が高く、"
    echo "  この後のpophug-install.sh側でも同様の警告が出ます。"
    echo "  Raspberry Pi Imagerの「カスタマイズ」でユーザー名を pophug にして"
    echo "  書き込み直すことを強く推奨します。"
    read -p "  それでも続行しますか？ (yes と入力): " CONFIRM_USER
    if [ "$CONFIRM_USER" != "yes" ]; then
        echo "中止しました"
        exit 1
    fi
fi

echo ""
echo "--- gitの確認 ---"
if ! command -v git > /dev/null 2>&1; then
    echo "  gitが見つからないためインストールします..."
    sudo apt-get update
    sudo apt-get install -y git
else
    echo "  gitは既にインストール済みです"
fi

echo ""
echo "--- リポジトリの取得 ---"
if [ -d "$TARGET_DIR/.git" ]; then
    echo "  $TARGET_DIR は既にgitリポジトリのようです。最新化します..."
    git -C "$TARGET_DIR" fetch origin
    git -C "$TARGET_DIR" reset --hard origin/main
elif [ -e "$TARGET_DIR" ]; then
    echo "  ⚠ $TARGET_DIR は既に存在しますが、gitリポジトリではないようです。"
    echo "    中身を確認の上、手動でリネームまたは削除してから再実行してください。"
    exit 1
else
    git clone "$REPO_URL" "$TARGET_DIR"
fi

echo ""
echo "--- pophug-install.sh の実行 ---"
cd "$TARGET_DIR"
bash pophug-install.sh

echo ""
echo "=== ブートストラップ完了 ==="
read -p "今すぐ再起動しますか？ (yes と入力): " CONFIRM_REBOOT
if [ "$CONFIRM_REBOOT" = "yes" ]; then
    echo "再起動します..."
    sudo reboot
else
    echo "再起動を見送りました。準備ができたら手動で次を実行してください: sudo reboot"
fi
