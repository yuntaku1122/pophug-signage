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
| `version.py` | バージョン情報・変更履歴 |

## セットアップ

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

`images/`フォルダにJPEG/PNGを置くと自動でスライドショーに反映される。
起動すると`http://<このマシンのIP>:8080`でアップロードページが開く。

## QRコードボタン

ラズパイ単体運用時、IPアドレスが分からずアップロードページにアクセスできない問題への対策。
`config.py`の`QR_BUTTON_GPIO_PIN`（デフォルトGPIO17、内部プルアップ・GND間にボタン接続）を押すと、
アップロードページのURLをQRコードで一時的に画面表示する。

GPIOが無い環境（Mac等）では、ウィンドウ選択中に**Qキー**を押すことで同じ動作を確認できる。

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
