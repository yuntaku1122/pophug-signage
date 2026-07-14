# ============================================
# KitchenCar POP Signage - upload_server.py
#
# iPhoneとRaspberry Pi間でBluetooth/NFCによる直接のファイル転送は
# 実用的でない（iOSのBluetooth OBEXは他社デバイス非対応、NFCは
# タグ書き込み程度しかできず画像送信には使えない）ため、
# 同一Wi-Fi上のWebページ経由でのアップロード方式を採用。
#
# 使い方:
#   1. iPhoneのSafariで http://<PiのIPアドレス>:8080 を開く
#   2. 写真を選択してアップロード → images/ フォルダに保存される
#   3. サイネージ側は数秒おきに自動で新しい画像を検知する
#
# さらに手軽にするには:
#   - iOS「ショートカット」アプリで共有シートから直接POSTする
#     ショートカットを作れば、写真アプリの共有ボタン一発で送信可能
#     （/upload エンドポイントにmultipart POSTするだけ）
#   - Raspberry Pi自体をWi-Fiアクセスポイント化(hostapd)すれば、
#     外部Wi-Fiが無い出店先でもiPhoneがPiに直接つながる
# ============================================

import os
from datetime import datetime

import signage_state

try:
    from config import RESCAN_INTERVAL
except ImportError:
    RESCAN_INTERVAL = 10

try:
    from config import TRANSITION_DURATION as DEFAULT_TRANSITION_DURATION
except ImportError:
    DEFAULT_TRANSITION_DURATION = 0.5

try:
    from version import __version__
except ImportError:
    __version__ = "unknown"

try:
    from flask import Flask, request, redirect, send_from_directory
except ImportError:
    Flask = None


UPLOAD_PAGE = """
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POP画像アップロード</title>
<style>
  body { font-family: -apple-system, sans-serif; background:#f5f5f0; margin:0; padding:24px; }
  h1 { font-size:20px; color:#228b22; }
  h2 { font-size:16px; color:#333; margin:28px 0 4px; }
  .hint { font-size:12px; color:#888; margin:0 0 12px; }
  .box { background:#fff; border-radius:12px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }
  input[type=file] { width:100%; margin:16px 0; }
  button { width:100%; padding:14px; background:#228b22; color:#fff; border:none;
           border-radius:8px; font-size:16px; }
  .msg { color:#228b22; font-weight:bold; }
  .gallery { display:grid; grid-template-columns:repeat(2, 1fr); gap:12px; margin-top:4px; }
  .item { background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 2px 6px rgba(0,0,0,0.08);
          transition:opacity .2s; }
  .item img { width:100%; height:140px; object-fit:cover; display:block; }
  .item.is-hidden img { opacity:0.3; filter:grayscale(100%); }
  .switch-row { display:flex; align-items:center; gap:10px; padding:10px 12px; }
  .switch { position:relative; display:inline-block; width:46px; height:26px; flex-shrink:0; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background-color:#ccc; transition:.2s; border-radius:26px; cursor:pointer; }
  .slider:before { position:absolute; content:""; height:20px; width:20px; left:3px; top:3px;
                    background-color:#fff; transition:.2s; border-radius:50%;
                    box-shadow:0 1px 2px rgba(0,0,0,0.3); }
  .switch input:checked + .slider { background-color:#228b22; }
  .switch input:checked + .slider:before { transform:translateX(20px); }
  .switch-label { font-size:13px; color:#555; }
  .switch-label.is-updating { color:#bbb; }
  .setting-row { margin-top:16px; }
  .setting-row label { font-size:14px; color:#333; display:flex; justify-content:space-between; }
  .setting-row input[type=range] { width:100%; margin:10px 0 4px; accent-color:#228b22; }
  .setting-status { font-size:12px; color:#999; min-height:16px; }
  .version-footer { text-align:center; font-size:11px; color:#bbb; margin:28px 0 8px; }
</style>
</head>
<body>
  <div class="box">
    <h1>POP画像アップロード</h1>
    __MESSAGE__
    <form method="POST" action="/upload" enctype="multipart/form-data">
      <input type="file" name="files" accept="image/*" multiple>
      <button type="submit">サイネージに送信</button>
    </form>
    <p style="font-size:12px;color:#999;">現在 __COUNT__ 枚登録 / うち __VISIBLE_COUNT__ 枚を表示中</p>
  </div>

  <h2>画像一覧</h2>
  <p class="hint">スイッチON＝サイネージに表示中。切り替えると即座に保存され、サイネージには最大__RESCAN_SEC__秒で反映されます。</p>
  <div class="gallery">
    __GALLERY__
  </div>

  <div class="box" style="margin-top:24px;">
    <h1>表示設定</h1>
    <div class="setting-row">
      <label>画面切り替えの速さ <span id="transition-value">__TRANSITION_DURATION__</span>秒</label>
      <input type="range" id="transition-slider" min="0.2" max="2.0" step="0.1" value="__TRANSITION_DURATION__">
      <p class="setting-status" id="transition-status"></p>
    </div>
  </div>

  <script>
  document.querySelectorAll('.toggle-cb').forEach(function (cb) {
    cb.addEventListener('change', function () {
      var filename = this.dataset.filename;
      var item = document.getElementById('item-' + filename);
      var label = item.querySelector('.switch-label');
      var wasChecked = this.checked;
      label.textContent = '更新中...';
      label.classList.add('is-updating');

      fetch('/toggle', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Accept': 'application/json'
        },
        body: 'filename=' + encodeURIComponent(filename)
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          item.classList.toggle('is-hidden', data.hidden);
          label.textContent = data.hidden ? '非表示中' : '表示中';
          label.classList.remove('is-updating');
        })
        .catch(function () {
          // 通信失敗時はスイッチを元の状態に戻す
          cb.checked = !wasChecked;
          label.textContent = '更新に失敗しました';
          label.classList.remove('is-updating');
        });
    });
  });
  </script>

  <script>
  (function () {
    var slider = document.getElementById('transition-slider');
    var valueLabel = document.getElementById('transition-value');
    var status = document.getElementById('transition-status');

    // ドラッグ中は数値表示だけ更新（通信は発生させない）
    slider.addEventListener('input', function () {
      valueLabel.textContent = parseFloat(this.value).toFixed(1);
    });

    // 指を離した(値が確定した)タイミングで保存する
    slider.addEventListener('change', function () {
      var val = parseFloat(this.value).toFixed(1);
      status.textContent = '保存中...';

      fetch('/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Accept': 'application/json'
        },
        body: 'transition_duration=' + encodeURIComponent(val)
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          valueLabel.textContent = parseFloat(data.transition_duration).toFixed(1);
          status.textContent = '保存しました（サイネージには数秒で反映されます）';
        })
        .catch(function () {
          status.textContent = '保存に失敗しました';
        });
    });
  })();
  </script>

  <p class="version-footer">KitchenCar POP Signage v__VERSION__</p>
</body>
</html>
"""


def render_gallery_item(filename, is_hidden):
    state_class = "is-hidden" if is_hidden else ""
    checked_attr = "" if is_hidden else "checked"
    label_text = "非表示中" if is_hidden else "表示中"
    return f"""
    <div class="item {state_class}" id="item-{filename}">
      <img src="/img/{filename}" loading="lazy">
      <div class="switch-row">
        <label class="switch">
          <input type="checkbox" class="toggle-cb" data-filename="{filename}" {checked_attr}>
          <span class="slider"></span>
        </label>
        <span class="switch-label">{label_text}</span>
      </div>
    </div>
    """


def create_app(image_folder):
    if Flask is None:
        raise RuntimeError("Flaskがインストールされていません。 pip install flask を実行してください。")

    app = Flask(__name__)
    os.makedirs(image_folder, exist_ok=True)
    supported = ('.jpg', '.jpeg', '.png')

    def list_images():
        return sorted(f for f in os.listdir(image_folder) if f.lower().endswith(supported))

    @app.route("/", methods=["GET"])
    def index():
        saved = request.args.get("saved")
        message = f'<p class="msg">{saved}枚アップロードしました</p>' if saved else ""

        files = list_images()
        hidden = signage_state.load_hidden(image_folder)
        visible_count = len([f for f in files if f not in hidden])
        gallery_html = "".join(render_gallery_item(f, f in hidden) for f in reversed(files))

        settings = signage_state.load_settings(
            image_folder, {"transition_duration": DEFAULT_TRANSITION_DURATION})
        transition_duration = f"{float(settings.get('transition_duration', DEFAULT_TRANSITION_DURATION)):.1f}"

        html = (UPLOAD_PAGE
                .replace("__MESSAGE__", message)
                .replace("__COUNT__", str(len(files)))
                .replace("__VISIBLE_COUNT__", str(visible_count))
                .replace("__RESCAN_SEC__", str(RESCAN_INTERVAL))
                .replace("__TRANSITION_DURATION__", transition_duration)
                .replace("__GALLERY__", gallery_html)
                .replace("__VERSION__", __version__))
        return html

    @app.route("/img/<path:filename>")
    def serve_image(filename):
        return send_from_directory(image_folder, filename)

    @app.route("/toggle", methods=["POST"])
    def toggle():
        filename = request.form.get("filename")
        is_hidden = False
        if filename:
            hidden_set = signage_state.toggle_hidden(image_folder, filename)
            is_hidden = filename in hidden_set

        if request.headers.get("Accept") == "application/json":
            return {"filename": filename, "hidden": is_hidden}, 200

        return redirect("/")

    @app.route("/settings", methods=["POST"])
    def update_settings():
        try:
            duration = float(request.form.get("transition_duration"))
        except (TypeError, ValueError):
            return {"error": "invalid transition_duration"}, 400

        # 想定外の値が来ても安全なようにクランプしておく
        duration = round(max(0.1, min(duration, 5.0)), 1)

        signage_state.save_settings(
            image_folder,
            {"transition_duration": duration},
            defaults={"transition_duration": DEFAULT_TRANSITION_DURATION},
        )

        if request.headers.get("Accept") == "application/json":
            return {"transition_duration": duration}, 200

        return redirect("/")

    @app.route("/upload", methods=["POST"])
    def upload():
        files = request.files.getlist("files")
        saved = 0
        for f in files:
            if not f or not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png"):
                continue
            ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")
            safe_name = f"upload_{ts}{ext}"
            f.save(os.path.join(image_folder, safe_name))
            saved += 1

        # iOSショートカットからのAPI的な呼び出しにも対応（JSON応答）
        if request.headers.get("Accept") == "application/json":
            return {"saved": saved}, 200

        return redirect(f"/?saved={saved}")

    return app


if __name__ == "__main__":
    # 単体テスト起動用: python upload_server.py
    app = create_app("./images")
    app.run(host="0.0.0.0", port=8080, debug=True)
