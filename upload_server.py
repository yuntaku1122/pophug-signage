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
    from config import IMAGE_INTERVAL as DEFAULT_IMAGE_INTERVAL
except ImportError:
    DEFAULT_IMAGE_INTERVAL = 12

try:
    from config import ROTATE_SCREEN as DEFAULT_ROTATION
except ImportError:
    DEFAULT_ROTATION = 0

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
  .rotate-preview-row { display:flex; justify-content:center; margin:16px 0; }
  .rotate-preview { width:40px; height:66px; border:3px solid #228b22; border-radius:5px;
                     transition:transform .3s; background:#f0f7f0; }
  .rotate-row { display:flex; gap:10px; }
  .rotate-row button { flex:1; background:#555; padding:14px; border:none; border-radius:8px;
                        color:#fff; font-size:15px; }
  .rotate-row button:active { background:#333; }
  .rotation-current { text-align:center; font-size:14px; color:#333; margin-bottom:4px; }
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
      <label>画面切り替えの速さ（トランジションの時間） <span id="transition-value">__TRANSITION_DURATION__</span>秒</label>
      <input type="range" id="transition-slider" min="0.2" max="2.0" step="0.1" value="__TRANSITION_DURATION__">
      <p class="setting-status" id="transition-status"></p>
    </div>
    <div class="setting-row">
      <label>画面切り替えの時間（1枚あたりの表示時間） <span id="interval-value">__IMAGE_INTERVAL__</span>秒</label>
      <input type="range" id="interval-slider" min="3" max="30" step="1" value="__IMAGE_INTERVAL__">
      <p class="setting-status" id="interval-status"></p>
    </div>
  </div>

  <div class="box" style="margin-top:16px;">
    <h1>画面の向き</h1>
    <p class="rotation-current">現在の設定: <span id="rotation-value">__ROTATION__</span>度</p>
    <div class="rotate-preview-row">
      <div class="rotate-preview" id="rotation-preview" style="transform:rotate(__ROTATION__deg);"></div>
    </div>
    <div class="rotate-row">
      <button type="button" id="rotate-left">⟲ 左に90度</button>
      <button type="button" id="rotate-right">⟳ 右に90度</button>
    </div>
    <p class="setting-status" id="rotation-status"></p>
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
  function setupSlider(sliderId, valueId, statusId, fieldName, decimals) {
    var slider = document.getElementById(sliderId);
    var valueLabel = document.getElementById(valueId);
    var status = document.getElementById(statusId);

    // ドラッグ中は数値表示だけ更新（通信は発生させない）
    slider.addEventListener('input', function () {
      valueLabel.textContent = parseFloat(this.value).toFixed(decimals);
    });

    // 指を離した(値が確定した)タイミングで保存する
    slider.addEventListener('change', function () {
      var val = parseFloat(this.value).toFixed(decimals);
      status.textContent = '保存中...';

      fetch('/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Accept': 'application/json'
        },
        body: fieldName + '=' + encodeURIComponent(val)
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          valueLabel.textContent = parseFloat(data[fieldName]).toFixed(decimals);
          status.textContent = '保存しました（サイネージには数秒で反映されます）';
        })
        .catch(function () {
          status.textContent = '保存に失敗しました';
        });
    });
  }

  setupSlider('transition-slider', 'transition-value', 'transition-status', 'transition_duration', 1);
  setupSlider('interval-slider', 'interval-value', 'interval-status', 'image_interval', 0);
  </script>

  <script>
  (function () {
    var valueLabel = document.getElementById('rotation-value');
    var preview = document.getElementById('rotation-preview');
    var status = document.getElementById('rotation-status');
    var leftBtn = document.getElementById('rotate-left');
    var rightBtn = document.getElementById('rotate-right');

    function rotate(direction) {
      leftBtn.disabled = true;
      rightBtn.disabled = true;
      status.textContent = '変更中...';

      fetch('/rotate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Accept': 'application/json'
        },
        body: 'direction=' + direction
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          valueLabel.textContent = data.rotation;
          preview.style.transform = 'rotate(' + data.rotation + 'deg)';
          status.textContent = data.rotation + '度に設定しました（サイネージには数秒で反映されます）';
        })
        .catch(function () {
          status.textContent = '変更に失敗しました';
        })
        .finally(function () {
          leftBtn.disabled = false;
          rightBtn.disabled = false;
        });
    }

    leftBtn.addEventListener('click', function () { rotate('left'); });
    rightBtn.addEventListener('click', function () { rotate('right'); });
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

        settings = signage_state.load_settings(image_folder, {
            "transition_duration": DEFAULT_TRANSITION_DURATION,
            "image_interval": DEFAULT_IMAGE_INTERVAL,
            "rotation": DEFAULT_ROTATION,
        })
        transition_duration = f"{float(settings.get('transition_duration', DEFAULT_TRANSITION_DURATION)):.1f}"
        image_interval = int(round(float(settings.get("image_interval", DEFAULT_IMAGE_INTERVAL))))
        rotation = int(settings.get("rotation", DEFAULT_ROTATION)) % 360

        html = (UPLOAD_PAGE
                .replace("__MESSAGE__", message)
                .replace("__COUNT__", str(len(files)))
                .replace("__VISIBLE_COUNT__", str(visible_count))
                .replace("__RESCAN_SEC__", str(RESCAN_INTERVAL))
                .replace("__TRANSITION_DURATION__", transition_duration)
                .replace("__IMAGE_INTERVAL__", str(image_interval))
                .replace("__ROTATION__", str(rotation))
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
        updates = {}

        if "transition_duration" in request.form:
            try:
                duration = float(request.form.get("transition_duration"))
            except (TypeError, ValueError):
                return {"error": "invalid transition_duration"}, 400
            updates["transition_duration"] = round(max(0.1, min(duration, 5.0)), 1)

        if "image_interval" in request.form:
            try:
                interval = float(request.form.get("image_interval"))
            except (TypeError, ValueError):
                return {"error": "invalid image_interval"}, 400
            updates["image_interval"] = round(max(2.0, min(interval, 60.0)), 1)

        if not updates:
            return {"error": "no valid fields"}, 400

        defaults = {
            "transition_duration": DEFAULT_TRANSITION_DURATION,
            "image_interval": DEFAULT_IMAGE_INTERVAL,
            "rotation": DEFAULT_ROTATION,
        }
        result = signage_state.save_settings(image_folder, updates, defaults=defaults)

        if request.headers.get("Accept") == "application/json":
            return {k: result[k] for k in updates}, 200

        return redirect("/")

    @app.route("/rotate", methods=["POST"])
    def rotate():
        direction = request.form.get("direction")
        if direction not in ("left", "right"):
            return {"error": "invalid direction"}, 400

        current = signage_state.load_settings(
            image_folder, {"rotation": DEFAULT_ROTATION}
        ).get("rotation", DEFAULT_ROTATION)

        delta = -90 if direction == "left" else 90
        new_rotation = (int(current) + delta) % 360

        signage_state.save_settings(
            image_folder,
            {"rotation": new_rotation},
            defaults={"transition_duration": DEFAULT_TRANSITION_DURATION, "rotation": DEFAULT_ROTATION},
        )

        if request.headers.get("Accept") == "application/json":
            return {"rotation": new_rotation}, 200

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
