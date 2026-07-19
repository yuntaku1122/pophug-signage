# pophug_signage

キッチンカー用ポータブルデジタルサイネージ。

縦長のPOP画像をクロスフェードで切り替えながら、モバイルディスプレイ + Raspberry Piで表示する。
画像はiPhoneから同一Wi-Fi（またはiPhoneテザリング）経由でワイヤレスにアップロードでき、
表示/非表示もスマホ上で切り替えられる。

## 構成

| ファイル | 役割 |
|---|---|
| `main.py` | pygameによるスライドショー本体。QRボタン機能も含む |
| `config.py` | 画面サイズ、間隔、GPIOピン番号などの設定 |
| `upload_server.py` | iPhoneからの画像アップロード用Webサーバー（Flask） |
| `signage_state.py` | 表示/非表示・トランジション速度などの状態（`images/.hidden.json`, `images/.settings.json`）の読み書き |
| `wifi_setup.py` | Wi-Fiセットアップモード関連（SSID自動生成、QRペイロード生成、netctl呼び出し） |
| `update_check.py` | アップデート確認・適用（GitHub Releases連携、ファイル入れ替え、ロールバック） |
| `scripts/pophug-netctl` | root権限が必要なネットワーク操作だけを行う限定ヘルパー（要インストール、後述） |
| `scripts/pophug-update-apply` | root権限が必要な更新の仕上げ（netctl入れ替え・サービス再起動）だけを行う限定ヘルパー（要インストール、後述） |
| `version.py` | バージョン情報・変更履歴 |
| `manual.py` | 取扱説明の内容（テキスト、ページ形式） |

## セットアップ

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

`images/`フォルダにJPEG/PNGを置くと自動でスライドショーに反映される。
起動すると`http://<このマシンのIP>:8080`でアップロードページが開く。

## QRコードボタン（4段階の長押しに対応）

1つのボタンで4つの操作ができる。**判定は「離した瞬間の合計長押し時間」**で行われ、
押している間は画面に「あと何秒で何が起きるか」の進捗が表示される。

| 操作 | 動作 |
|---|---|
| 短押し | アップロードページのQRコードを表示/非表示トグル |
| `MANUAL_HOLD_SECONDS`秒（デフォルト2秒）以上長押し | 取扱説明を表示 |
| `WIFI_SETUP_HOLD_SECONDS`秒（デフォルト5秒）以上長押し | Wi-Fiセットアップモードに入る |
| `SHUTDOWN_HOLD_SECONDS`秒（デフォルト10秒）以上長押し | シャットダウン |

Wi-Fiセットアップモード・取扱説明の表示中にボタンを押すと、押した時間に関わらず
キャンセル（閉じる）操作になる。

GPIOが無い環境（Mac等）では、ウィンドウ選択中に**Qキー**でQR表示、**Mキー**で取扱説明、
**Wキー**でWi-Fiセットアップモード、**Sキー**でシャットダウンをそれぞれ即座に確認できる
（実機と違い長押し判定は無い）。

## 取扱説明機能（説明書レス運用）

`images/`フォルダに表示できる写真が1枚も無い場合（購入直後の無垢な状態）、
「画像がありません」の代わりに`manual.py`に書かれた取扱説明を自動的にループ表示する。
写真を1枚でも送ると、自動的に通常のスライドショーに切り替わる。

取扱説明はいつでもボタンの2〜5秒長押しで呼び出せる（写真が既に登録済みの状態でも可能）。
内容はコードと同じくアップデート機能で配布されるため、印刷物の説明書を作り直すことなく
更新できる。

## シャットダウンボタン

アップロードページ下部の「システム」から、確認ダイアログを経てラズパイをシャットダウンできる。
Flaskサーバーは一般ユーザー(`pophug`)権限で動いているため、事前に**シャットダウンコマンドだけを
パスワード無しで実行できるよう**限定的に許可しておく必要がある。

```bash
which shutdown   # 表示されたパスを次のコマンドで使う（多くは /sbin/shutdown）
echo "pophug ALL=(ALL) NOPASSWD: /sbin/shutdown -h now" | sudo tee /etc/sudoers.d/pophug-shutdown
sudo chmod 440 /etc/sudoers.d/pophug-shutdown
sudo visudo -c    # "parsed OK" と出れば設定完了
```

`upload_server.py`の`SHUTDOWN_COMMAND`と、上記sudoersのパスは必ず一致させること。

## Wi-Fiセットアップモード（モニター/キーボード不要でのWi-Fi設定）

「無垢な状態」のPiでも、スマホだけで新しい出店先のWi-Fiを設定できる機能。QRボタンを
**3秒以上長押し**すると、Piが一時的に自分専用のアクセスポイントを立てる。

1. 画面にQRコード（読み取ると直接そのWi-Fiに繋がる）とSSID/パスワードが表示される
2. スマホをそのWi-Fiに繋ぎ、画面のURL（`http://10.42.0.1:8080/wifi`）を開く
3. 周辺のWi-Fi一覧から選ぶ（または手動入力）→パスワード入力→接続
4. 接続に成功すると、Piは自動的にアクセスポイントを終了して通常運用に戻る
5. 途中でやめたい時はボタンを押すとキャンセルできる（10分操作が無くても自動キャンセル）

セットアップ用アクセスポイントのSSIDは、**機体のMACアドレス下4桁**を使って自動的に一意になる
（例: `pophug-setup-3F2A`）。同じ製品を複数台並べても電波が衝突しない。パスワードは
`config.py`の`WIFI_SETUP_DEFAULT_PASSWORD`が初期値（アップロードページの「ネットワーク」から
後で変更可能）。

### インストール（Pi側で1回だけ必要）

ネットワーク設定の変更にはroot権限が必要なため、限定的な操作だけを行うヘルパースクリプトを
配置し、それだけをsudoで許可する。

```bash
sudo cp scripts/pophug-netctl /usr/local/bin/
sudo chmod 755 /usr/local/bin/pophug-netctl
sudo chown root:root /usr/local/bin/pophug-netctl

echo "pophug ALL=(ALL) NOPASSWD: /usr/local/bin/pophug-netctl *" | sudo tee /etc/sudoers.d/pophug-netctl
sudo chmod 440 /etc/sudoers.d/pophug-netctl
sudo visudo -c    # "parsed OK" と出れば設定完了
```

`pophug-netctl`自体が、実行できるコマンド（アクセスポイントの起動/停止、Wi-Fiスキャン、
接続）とその引数の形式を厳しく制限しているため、`*`で任意引数を許可していても、
それ以上の任意コマンド実行はできない設計になっている。

## アップデート機能（不特定多数への販売を見据えた配信の仕組み）

お客さんのPiに開発者用のGit操作をしてもらわずに、**スマホの設定画面から更新できる**仕組み。
GitHubの「Releases」機能をそのまま配信先として使う（本リポジトリは公開リポジトリである
必要がある）。

### 開発者側: 新バージョンをリリースする手順

```bash
# 1. version.pyの__version__とCHANGELOGを更新してコミット・push（通常の開発フローと同じ）
git add -A
git commit -m "..."
git push

# 2. タグを打ってpush
git tag v4.4.0
git push origin v4.4.0

# 3. GitHub上でそのタグから「Release」を作成する（GitHubのWeb画面 or `gh release create v4.4.0`）
#    本文にCHANGELOGの内容を書いておくと、お客さんのスマホ画面にそのまま表示される
```

これだけで完了。別途ビルドやパッケージのアップロードは不要（GitHubがタグの中身を
自動的にアーカイブ化してくれる）。

### お客さん側: 更新方法

アップロードページの「アップデート」から「最新バージョンを確認」→（あれば）変更内容を見て
「アップデートする」を押すだけ。`images/`フォルダと`venv/`は温存されるため、
アップロード済みの写真や設定が消えることはない。新しいコードに明らかな不具合があれば
自動的に元のバージョンへロールバックする。

### インストール（Pi側で1回だけ必要）

```bash
sudo cp scripts/pophug-update-apply /usr/local/bin/
sudo chmod 755 /usr/local/bin/pophug-update-apply
sudo chown root:root /usr/local/bin/pophug-update-apply

echo "pophug ALL=(ALL) NOPASSWD: /usr/local/bin/pophug-update-apply *" | sudo tee /etc/sudoers.d/pophug-update-apply
sudo chmod 440 /etc/sudoers.d/pophug-update-apply
sudo visudo -c
```

`config.py`の`GITHUB_REPO`（`"ユーザー名/リポジトリ名"`の形式）が、実際に使うリポジトリと
一致していることを確認しておくこと。

## 動作環境

- Mac: 検証用。pygameがPython 3.14に未対応のため、**Python 3.12系のvenv**で運用すること
- Raspberry Pi: Zero 2 W想定（低消費電力・低発熱のため）。`config.py`の`FULLSCREEN = True`に変更して運用

## バージョン確認

バージョンは`version.py`で一元管理している。確認方法は3通り。

```bash
python3 main.py --version
```
- サイネージ起動時のログ（`KitchenCar POP Signage v3.4.0 起動`）
- アップロードページ（`http://<IP>:8080`）の一番下

## 変更履歴

| バージョン | 内容 |
|---|---|
| 4.6.2 | アップデート機能の根本原因を修正。systemctl restartが呼び出し元プロセスを道連れにして強制終了させ、成功していた更新が誤ってロールバックされる不具合を修正（--no-block化） |
| 4.6.1 | 取扱説明の文言を修正（「ようこそ pophug へ」→「ようこそpophug サイネージへ」） |
| 4.6.0 | アップデート機能の不具合を修正。進行状況をファイルに永続化する方式に変更し、プロセス再起動をまたいでも正しく結果が伝わるように。詳細ログを追加し、QR/取扱説明/Wi-Fiセットアップ画面に現在バージョンを常時表示 |
| 4.5.0 | 説明書レス運用のため取扱説明機能を追加。写真が無い時は自動表示、ボタン長押しでいつでも呼び出せる。ボタンの長押しを4段階に再編 |
| 4.4.0 | スマホから操作できるアップデート機能を追加。GitHub Releasesを配信先とし、自動ロールバック対応 |
| 4.3.0 | Wi-Fi接続失敗の原因（netplan等で既に作られた壊れた接続プロファイルとの競合）を修正。スマホ側に接続の成功/失敗を実際に表示するように改善 |
| 4.2.0 | Wi-Fiセットアップ画面に、設定ページ(/wifi)を直接開けるQRコードを追加。Wi-Fi接続用QRと2つ並べて表示 |
| 4.1.0 | QRボタンにシャットダウン機能を追加（8秒以上長押し）。長押し判定を離した瞬間の合計時間基準に変更し、押している間は進捗を画面表示 |
| 4.0.0 | ボタン長押しで入る「Wi-Fiセットアップモード」を追加。モニター/キーボード無しでもスマホだけで無垢な状態のPiに新しいWi-Fiを設定できるように |
| 3.9.0 | QRコード表示中はスライドショーを一時停止するように変更。Web側での表示切替・設定変更を検知したらQRを自動的に閉じてスライドショーを再開 |
| 3.8.0 | Webページに確認ダイアログ付きのシャットダウンボタンを追加 |
| 3.7.0 | 画面切り替え効果にスライド（上下左右にスワイプ風）を追加し、Webのプルダウンでフェード/スライドを選択できるように |
| 3.6.0 | Webページに「画面切り替えの時間（1枚あたりの表示時間）」のスライダーを追加。トランジション速度と併せて個別に調整可能に |
| 3.5.0 | Webページに画面回転の切替ボタンを追加（左右90度ずつ、現在の向きをプレビュー表示） |
| 3.4.0 | 表示切替・設定変更の反映を1秒以内に高速化／状態ファイルの保存をアトミック書き込みに変更（電源断対策）／Webにトランジション速度のスライダーを追加 |
| 3.3.0 | 画面回転(`ROTATE_SCREEN`)・画面いっぱいに引き伸ばすstretch表示に対応／クロスフェードを実時間基準に修正 |
| 3.2.0 | アップロードページに画像ごとの表示/非表示スイッチを追加 |
| 3.1.0 | QRコード表示ボタン(GPIO)を追加、iPhoneからのワイヤレスアップロード機能を実装 |
| 3.0.0 | QR決済表示を廃止し、POP画像スライドショー専用のポータブルデジタルサイネージに方針転換 |
