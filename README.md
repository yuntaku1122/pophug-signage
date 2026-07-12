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
| `signage_state.py` | 画像の表示/非表示状態（`images/.hidden.json`）の読み書き |

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

## 動作環境

- Mac: 検証用。pygameがPython 3.14に未対応のため、**Python 3.12系のvenv**で運用すること
- Raspberry Pi: Zero 2 W想定（低消費電力・低発熱のため）。`config.py`の`FULLSCREEN = True`に変更して運用
