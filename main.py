# ============================================
# KitchenCar POP Signage - main.py
# バージョンは version.py で一元管理（このファイルには書かない）
# ============================================

import os
os.environ["SDL_VIDEO_WINDOW_POS"] = "100,100"
import pygame
import sys
import random
import time
import io
import subprocess
import threading
from datetime import datetime
from config import *
from signage_state import load_hidden, hidden_mtime, load_settings, settings_mtime
from version import __version__
import wifi_setup
from manual import MANUAL_PAGES

try:
    import qrcode
except ImportError:
    qrcode = None

def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")

_font_path_cache = {"path": None, "searched": False}

def get_japanese_font(size):
    if not _font_path_cache["searched"]:
        font_candidates = [
            "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
            "/System/Library/Fonts/Hiragino Maru Gothic ProN W4.ttc",
            "/Library/Fonts/ヒラギノ丸ゴ Pro W4.otf",
            "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",  # Raspberry Pi OS想定
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/Library/Fonts/Arial Unicode MS.ttf",
        ]
        for font_path in font_candidates:
            if os.path.exists(font_path):
                _font_path_cache["path"] = font_path
                break
        _font_path_cache["searched"] = True
        if _font_path_cache["path"]:
            log(f"フォント読み込み成功: {_font_path_cache['path']}")
        else:
            log("日本語フォントが見つからず、システムデフォルトフォントを使用します")

    path = _font_path_cache["path"]
    if path:
        try:
            return pygame.font.Font(path, size)
        except Exception:
            pass
    return pygame.font.SysFont(None, size)


class PopSignage:
    def __init__(self):
        pygame.init()
        pygame.font.init()
        pygame.mouse.set_visible(False)

        flags = pygame.FULLSCREEN if FULLSCREEN else 0
        if FULLSCREEN:
            self.screen = pygame.display.set_mode((0, 0), flags)
        else:
            self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))

        # ROTATE_SCREENが90/270の場合、物理的な取り付け向きにより
        # 「論理的な描画キャンバス」は実ディスプレイと縦横が入れ替わる。
        # 全ての描画はこのcanvasに対して行い、最後にrun()内で回転させてscreenへ転写する。
        sw, sh = self.screen.get_width(), self.screen.get_height()
        if ROTATE_SCREEN in (90, 270):
            self.canvas = pygame.Surface((sh, sw))
        else:
            self.canvas = pygame.Surface((sw, sh))

        pygame.display.set_caption(f"KitchenCar POP Signage v{__version__}")
        self.clock = pygame.time.Clock()
        self.font_medium = get_japanese_font(36)
        self.font_small = get_japanese_font(24)

        self.pop_images = []          # 表示用にスケール済みSurfaceのリスト
        self._image_cache = {}        # ファイル名 -> スケール済みSurface（変更が無ければ再利用）
        self._image_mtimes = {}       # ファイル名 -> mtime（再スキャン判定用）
        self.current_pop_index = 0
        self.next_pop_index = 0
        self.pop_start_time = time.time()
        self.transition_start_time = 0
        self.in_transition = False
        self.last_scan_time = 0
        self.last_hidden_check_time = 0
        self.last_hidden_mtime = hidden_mtime(IMAGE_FOLDER)
        self._lock = threading.Lock()

        # Web側で変更されたトランジション時間・画面回転などの設定を読み込んで反映
        self.last_settings_mtime = settings_mtime(IMAGE_FOLDER)
        self._apply_settings()

        self.qr_active = False        # QRコードオーバーレイの表示中フラグ
        self.qr_hide_time = 0
        self.qr_surface = None
        self.qr_label_surface = None
        self.qr_url_surface = None
        self.qr_url = ""
        self._qr_pause_start = None   # QR表示開始時刻（スライドショー一時停止分の巻き戻しに使う）

        self.manual_active = False    # ボタン長押しで手動表示中かどうか
        self.manual_page_index = 0
        self.manual_page_start_time = 0

        self.wifi_setup_active = False
        self.wifi_setup_start_time = 0
        self.wifi_setup_ssid = ""
        self.wifi_setup_password = ""
        self.wifi_setup_qr_wifi = None     # ①: 直接Wi-Fiに繋がるQR
        self.wifi_setup_qr_url = None      # ②: 設定ページを開くQR
        self.wifi_setup_qr_labels = []
        self.wifi_setup_text_surfaces = []
        self.last_wifi_setup_check_time = 0

        self.load_pop_images(initial=True)

        if UPLOAD_ENABLED:
            self.start_upload_server()

        if QR_BUTTON_ENABLED:
            self.setup_qr_button()

    # ---------------- 画像読み込み ----------------

    def _apply_settings(self):
        """images/.settings.json の内容を読み込み、実行中の設定（TRANSITION_DURATION・
        IMAGE_INTERVAL・ROTATE_SCREENなど）に反映する。Web側の操作で変更された値をここで取り込む。"""
        settings = load_settings(IMAGE_FOLDER, {
            "transition_duration": TRANSITION_DURATION,
            "image_interval": IMAGE_INTERVAL,
            "transition_type": TRANSITION_TYPE,
            "rotation": ROTATE_SCREEN,
        })

        new_duration = settings.get("transition_duration", TRANSITION_DURATION)
        if new_duration != globals().get("TRANSITION_DURATION"):
            globals()["TRANSITION_DURATION"] = new_duration
            log(f"トランジション時間を更新: {new_duration}秒")

        new_interval = settings.get("image_interval", IMAGE_INTERVAL)
        if new_interval != globals().get("IMAGE_INTERVAL"):
            globals()["IMAGE_INTERVAL"] = new_interval
            log(f"画像切り替え時間を更新: {new_interval}秒")

        new_type = settings.get("transition_type", TRANSITION_TYPE)
        if new_type != globals().get("TRANSITION_TYPE"):
            globals()["TRANSITION_TYPE"] = new_type
            log(f"トランジションの種類を更新: {new_type}")

        new_rotation = settings.get("rotation", ROTATE_SCREEN)
        if new_rotation != globals().get("ROTATE_SCREEN"):
            globals()["ROTATE_SCREEN"] = new_rotation
            log(f"画面回転を更新: {new_rotation}度")
            self._rebuild_canvas()

    def _rebuild_canvas(self):
        """ROTATE_SCREENの変更を受けて、描画キャンバスを作り直す。
        縦横比が変わるため、キャッシュ済みの画像も破棄して新サイズで再生成させる。"""
        sw, sh = self.screen.get_width(), self.screen.get_height()
        if ROTATE_SCREEN in (90, 270):
            self.canvas = pygame.Surface((sh, sw))
        else:
            self.canvas = pygame.Surface((sw, sh))

        with self._lock:
            self._image_cache = {}
            self._image_mtimes = {}
        self.load_pop_images(initial=True)

    def load_pop_images(self, initial=False):
        """images/ フォルダを読み込み、新しい画像があれば反映する。
        アップロードサーバーから随時追加される画像を検知するため定期的に呼ばれる。"""
        supported = ('.jpg', '.jpeg', '.png')
        if not os.path.exists(IMAGE_FOLDER):
            os.makedirs(IMAGE_FOLDER)

        files = sorted(f for f in os.listdir(IMAGE_FOLDER) if f.lower().endswith(supported))
        hidden = load_hidden(IMAGE_FOLDER)
        files = [f for f in files if f not in hidden]
        mtimes = {f: os.path.getmtime(os.path.join(IMAGE_FOLDER, f)) for f in files}

        if mtimes == self._image_mtimes:
            return  # 変化なし

        w = self.canvas.get_width()
        h = self.canvas.get_height()
        new_cache = {}
        for f in files:
            if f in self._image_cache and self._image_mtimes.get(f) == mtimes[f]:
                # ファイル自体は変わっていない（表示/非表示の切替だけ）ので再デコードしない
                new_cache[f] = self._image_cache[f]
                continue
            path = os.path.join(IMAGE_FOLDER, f)
            try:
                img = pygame.image.load(path).convert()
                img = self._fit_image(img, w, h)
                new_cache[f] = img
            except Exception as e:
                log(f"画像読み込みエラー: {f} - {e}")

        new_images = [new_cache[f] for f in files if f in new_cache]

        with self._lock:
            self.pop_images = new_images
            self._image_cache = new_cache
            self._image_mtimes = mtimes
            if self.current_pop_index >= len(self.pop_images):
                self.current_pop_index = 0
            self.next_pop_index = self.current_pop_index

        if not initial:
            log(f"画像フォルダを再スキャン: {len(new_images)}枚 検出")

    @staticmethod
    def _fit_image(img, w, h):
        """IMAGE_FIT_MODEに応じて画像をスケーリングし、画面サイズのSurfaceを返す。
        contain: 画像全体が欠けずに収まるよう縮小し、余白はBG_COLORで塗る
        cover  : 画面いっぱいに敷き詰め、はみ出た部分はトリミングする
        stretch: アスペクト比を無視して画面ぴったりに引き伸ばす（余白・トリミングなし）"""
        iw, ih = img.get_size()
        canvas = pygame.Surface((w, h))
        canvas.fill(BG_COLOR)

        if IMAGE_FIT_MODE == "stretch":
            scaled = pygame.transform.smoothscale(img, (w, h))
            canvas.blit(scaled, (0, 0))
        elif IMAGE_FIT_MODE == "cover":
            scale = max(w / iw, h / ih)
            new_size = (int(iw * scale), int(ih * scale))
            scaled = pygame.transform.smoothscale(img, new_size)
            x = (new_size[0] - w) // 2
            y = (new_size[1] - h) // 2
            canvas.blit(scaled, (0, 0), area=pygame.Rect(x, y, w, h))
        else:  # "contain"
            scale = min(w / iw, h / ih)
            new_size = (max(1, int(iw * scale)), max(1, int(ih * scale)))
            scaled = pygame.transform.smoothscale(img, new_size)
            x = (w - new_size[0]) // 2
            y = (h - new_size[1]) // 2
            canvas.blit(scaled, (x, y))

        return canvas

    # ---------------- ワイヤレスアップロードサーバー ----------------

    def start_upload_server(self):
        """iPhoneからWi-Fi経由で画像をアップロードできる簡易Webサーバーを別スレッドで起動"""
        try:
            from upload_server import create_app
        except ImportError:
            log("upload_server.py が見つかりません。アップロード機能はスキップします")
            return

        app = create_app(IMAGE_FOLDER)

        def run():
            app.run(host=UPLOAD_HOST, port=UPLOAD_PORT, debug=False, use_reloader=False)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        log(f"アップロードサーバー起動: http://{self._get_local_ip()}:{UPLOAD_PORT}  (同一Wi-FiのiPhoneから開く)")

    @staticmethod
    def _get_local_ip():
        """同一LAN内から到達可能な自分のIPアドレスを推定する"""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"
        finally:
            s.close()

    # ---------------- QRコード表示ボタン ----------------

    def setup_qr_button(self):
        """ボタン1つで4段階の操作を行う。
        短押し             : QRコード表示/非表示トグル
        MANUAL_HOLD_SECONDS秒以上長押し     : 取扱説明を表示
        WIFI_SETUP_HOLD_SECONDS秒以上長押し : Wi-Fiセットアップモード
        SHUTDOWN_HOLD_SECONDS秒以上長押し   : シャットダウン
        判定は「離した瞬間の合計長押し時間」で行う（押している間は毎フレーム
        run()から_poll_button()が呼ばれ、進捗を画面に表示する）。
        ラズパイ実機ではGPIOボタン、Mac等GPIOが無い環境では
        Qキー(QR表示)・Mキー(取扱説明)・Wキー(Wi-Fiセットアップモード)・Sキー(シャットダウン)で代用する。"""
        self._qr_button = None
        self._button_press_start = None
        try:
            from gpiozero import Button
            self._qr_button = Button(QR_BUTTON_GPIO_PIN, pull_up=True, bounce_time=0.2)
            log(f"QRボタン待受け開始（GPIO{QR_BUTTON_GPIO_PIN}）: "
                f"短押し=QR表示 / {MANUAL_HOLD_SECONDS}秒長押し=取扱説明 / "
                f"{WIFI_SETUP_HOLD_SECONDS}秒長押し=Wi-Fiセットアップ / "
                f"{SHUTDOWN_HOLD_SECONDS}秒長押し=シャットダウン")
        except Exception as e:
            log(f"GPIOボタンが利用できません（{e}）。"
                f"代わりにキーボードの[Q]キーでQR表示、[M]キーで取扱説明、"
                f"[W]キーでWi-Fiセットアップ、[S]キーでシャットダウンを確認できます")

    def _poll_button(self):
        """毎フレーム呼ばれ、物理ボタンの押下時間を監視する。gpiozeroのコールバックではなく
        ポーリング方式にしているのは、長押し中の残り時間を画面に表示したいため。"""
        if self._qr_button is None:
            return

        now = time.time()
        pressed = self._qr_button.is_pressed

        if pressed and self._button_press_start is None:
            self._button_press_start = now
        elif not pressed and self._button_press_start is not None:
            held = now - self._button_press_start
            self._button_press_start = None
            self._handle_button_release(held)

    def _handle_button_release(self, held_seconds):
        """ボタンが離された時、押していた時間に応じた動作を実行する"""
        if self.wifi_setup_active:
            # セットアップモード中は、押した時間に関わらずキャンセル操作として扱う
            self.exit_wifi_setup_mode()
            return

        if self.manual_active:
            # 取扱説明の表示中は、押した時間に関わらず閉じる操作として扱う
            self.toggle_manual()
            return

        if held_seconds >= SHUTDOWN_HOLD_SECONDS:
            self._trigger_button_shutdown()
        elif held_seconds >= WIFI_SETUP_HOLD_SECONDS:
            self.enter_wifi_setup_mode()
        elif held_seconds >= MANUAL_HOLD_SECONDS:
            self.toggle_manual()
        else:
            self.toggle_qr_code()

    def _trigger_button_shutdown(self):
        """物理ボタンの長押しでシャットダウンを実行する（Web版と同じsudoersの許可を利用）"""
        log("ボタン長押しによるシャットダウン要求を受け付けました")
        self._hide_qr()
        self.manual_active = False

        # すぐに反映されるよう、シャットダウン中である旨を1フレーム描画してからflipする
        self.canvas.fill((10, 10, 10))
        surf = self._render_fit_text(
            "シャットダウンしています...", self.canvas.get_width() - 48,
            start_size=30, min_size=16, color=(255, 120, 120))
        self.canvas.blit(surf, (
            self.canvas.get_width() // 2 - surf.get_width() // 2,
            self.canvas.get_height() // 2 - surf.get_height() // 2))
        if ROTATE_SCREEN:
            rotated = pygame.transform.rotate(self.canvas, -ROTATE_SCREEN)
            self.screen.blit(rotated, (0, 0))
        else:
            self.screen.blit(self.canvas, (0, 0))
        pygame.display.flip()

        def do_shutdown():
            time.sleep(1)  # 画面の更新が確実に反映されてから実行する
            try:
                subprocess.run(SHUTDOWN_COMMAND, check=True, capture_output=True, text=True, timeout=15)
            except Exception as e:
                log(f"シャットダウンに失敗しました: {e}")

        threading.Thread(target=do_shutdown, daemon=True).start()

    def draw_button_hold_overlay(self):
        """ボタンを押している最中、あとどれだけ押せば何が起きるかを画面に表示する"""
        if self._button_press_start is None:
            return
        held = time.time() - self._button_press_start
        if held < 0.6:
            return  # QR表示用の短押しの邪魔をしないよう、少し経ってから表示する

        w = self.canvas.get_width()
        h = self.canvas.get_height()
        overlay = pygame.Surface((w, h))
        overlay.set_alpha(210)
        overlay.fill((10, 10, 10))
        self.canvas.blit(overlay, (0, 0))

        if held >= SHUTDOWN_HOLD_SECONDS:
            text = "離すとシャットダウンします"
            color = (255, 90, 90)
        elif held >= WIFI_SETUP_HOLD_SECONDS:
            remaining = SHUTDOWN_HOLD_SECONDS - held
            text = f"離すとWi-Fiセットアップモード（あと{remaining:.0f}秒でシャットダウン）"
            color = (255, 210, 120)
        elif held >= MANUAL_HOLD_SECONDS:
            remaining = WIFI_SETUP_HOLD_SECONDS - held
            text = f"離すと取扱説明を表示（あと{remaining:.0f}秒でWi-Fiセットアップ）"
            color = (150, 210, 255)
        else:
            remaining = MANUAL_HOLD_SECONDS - held
            text = f"長押し中...（あと{remaining:.1f}秒で取扱説明）"
            color = (255, 255, 255)

        surf = self._render_fit_text(text, w - 48, start_size=28, min_size=14, color=color)
        self.canvas.blit(surf, (w // 2 - surf.get_width() // 2, h // 2 - surf.get_height() // 2))

    def toggle_qr_code(self):
        """QR表示中にもう一度押されたら即座に消す。非表示中なら新たに表示する。"""
        if self.wifi_setup_active:
            # セットアップモード中はボタンをキャンセル操作として扱う
            self.exit_wifi_setup_mode()
            return
        if self.qr_active:
            self._hide_qr()
            log("QRコード非表示（ボタン再押下）")
        else:
            self.show_qr_code()

    def _hide_qr(self):
        """QRコードを非表示にする。QR表示中に一時停止していたスライドショーの
        タイマーを、停止していた時間分だけ巻き戻して違和感なく再開させる。"""
        if not self.qr_active:
            return
        if self._qr_pause_start is not None:
            paused = time.time() - self._qr_pause_start
            self.pop_start_time += paused
            if self.in_transition:
                self.transition_start_time += paused
            self._qr_pause_start = None
        self.qr_active = False

    def show_qr_code(self):
        """アップロードページのURLをQRコードとして生成し、画面に一定時間オーバーレイ表示する"""
        if qrcode is None:
            log("qrcodeライブラリが未インストールのためQR表示できません（pip install qrcode[pil]）")
            return

        url = UPLOAD_URL_OVERRIDE or f"http://{self._get_local_ip()}:{UPLOAD_PORT}"
        try:
            qr = qrcode.QRCode(box_size=8, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            qr_img.save(buf, format="PNG")
            buf.seek(0)
            surface = pygame.image.load(buf)

            # QR本体は画面の短辺の45%程度に収め、下のラベル・URL文字列も
            # 必ず表示できるよう余白を確保する
            size = int(min(self.canvas.get_width(), self.canvas.get_height()) * 0.45)
            surface = pygame.transform.scale(surface, (size, size))

            max_text_width = self.canvas.get_width() - 48
            self.qr_surface = surface
            self.qr_url = url
            self.qr_label_surface = self._render_fit_text(
                "写真アップロードはこちら", max_text_width, start_size=32, min_size=16)
            self.qr_url_surface = self._render_fit_text(
                url, max_text_width, start_size=22, min_size=12, color=(210, 210, 210))
            self.qr_active = True
            self.qr_hide_time = time.time() + QR_DISPLAY_SECONDS
            self._qr_pause_start = time.time()  # スライドショー一時停止の起点を記録
            log(f"QRコード表示: {url}（スライドショーを一時停止）")
        except Exception as e:
            log(f"QRコード生成エラー: {e}")

    @staticmethod
    def _render_fit_text(text, max_width, start_size, min_size=12, color=(255, 255, 255)):
        """指定した最大幅に収まるまでフォントサイズを段階的に縮小してレンダリングする"""
        size = start_size
        surf = None
        while size >= min_size:
            font = get_japanese_font(size)
            surf = font.render(text, True, color)
            if surf.get_width() <= max_width:
                return surf
            size -= 2
        return surf

    def draw_qr_overlay(self):
        if time.time() >= self.qr_hide_time:
            self._hide_qr()
            return

        w = self.canvas.get_width()
        h = self.canvas.get_height()

        overlay = pygame.Surface((w, h))
        overlay.set_alpha(235)
        overlay.fill((20, 20, 20))
        self.canvas.blit(overlay, (0, 0))

        qr = self.qr_surface
        label = self.qr_label_surface
        url_s = self.qr_url_surface

        gap = 18
        total_h = qr.get_height() + gap + label.get_height() + 10 + url_s.get_height()
        top = max(20, h // 2 - total_h // 2)

        qr_x = w // 2 - qr.get_width() // 2
        qr_y = top

        white_bg = pygame.Surface((qr.get_width() + 24, qr.get_height() + 24))
        white_bg.fill((255, 255, 255))
        self.canvas.blit(white_bg, (qr_x - 12, qr_y - 12))
        self.canvas.blit(qr, (qr_x, qr_y))

        label_y = qr_y + qr.get_height() + gap
        self.canvas.blit(label, (w // 2 - label.get_width() // 2, label_y))

        url_y = label_y + label.get_height() + 10
        self.canvas.blit(url_s, (w // 2 - url_s.get_width() // 2, url_y))

        self._draw_version_watermark()

    def _draw_version_watermark(self):
        """画面右下に小さくバージョン番号を表示する（QR/取扱説明/Wi-Fiセットアップ画面用。
        Webページを開かなくても、画面を見ただけで今のバージョンが分かるようにするため）"""
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        surf = get_japanese_font(14).render(f"v{__version__}", True, (140, 140, 140))
        self.canvas.blit(surf, (w - surf.get_width() - 12, h - surf.get_height() - 10))

    # ---------------- 取扱説明モード ----------------

    def toggle_manual(self):
        """ボタンの中段長押しで、取扱説明の表示/非表示をトグルする"""
        if self.manual_active:
            self.manual_active = False
            log("取扱説明を非表示にしました")
        else:
            self._hide_qr()
            self.manual_active = True
            self.manual_page_index = 0
            self.manual_page_start_time = time.time()
            log("取扱説明を表示しました")

    @staticmethod
    def _wrap_text_lines(text, font, max_width):
        """1行のテキストを、指定フォント・幅に収まるよう1文字ずつ折り返す
        （日本語は単語間にスペースが無いため、文字単位での折り返しが適切）"""
        wrapped = []
        current = ""
        for ch in text:
            test = current + ch
            if font.size(test)[0] > max_width and current:
                wrapped.append(current)
                current = ch
            else:
                current = test
        if current:
            wrapped.append(current)
        return wrapped

    def draw_manual_screen(self):
        """取扱説明の現在のページを描画し、一定時間ごとに自動でページ送りする。
        （写真が1枚も無い時の自動表示、ボタン長押しでの手動表示、両方から呼ばれる）"""
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        self.canvas.fill((24, 24, 30))

        if not MANUAL_PAGES:
            return

        now = time.time()
        if now - self.manual_page_start_time >= MANUAL_PAGE_SECONDS:
            self.manual_page_start_time = now
            self.manual_page_index = (self.manual_page_index + 1) % len(MANUAL_PAGES)

        page = MANUAL_PAGES[self.manual_page_index % len(MANUAL_PAGES)]
        max_width = w - 64

        title_surf = self._render_fit_text(
            page["title"], max_width, start_size=32, min_size=18, color=(255, 210, 110))

        body_font = get_japanese_font(22)
        body_surfaces = []
        for raw_line in page["body"].split("\n"):
            if not raw_line:
                body_surfaces.append(None)  # 空行は余白として扱う
                continue
            for wrapped_line in self._wrap_text_lines(raw_line, body_font, max_width):
                body_surfaces.append(body_font.render(wrapped_line, True, (225, 225, 225)))

        footer_surf = self._render_fit_text(
            f"{self.manual_page_index + 1} / {len(MANUAL_PAGES)}　（ボタンで閉じる）",
            max_width, start_size=16, min_size=11, color=(150, 150, 150))

        total_h = title_surf.get_height() + 26
        for s in body_surfaces:
            total_h += (s.get_height() if s is not None else 12) + 8

        y = max(20, h // 2 - total_h // 2)
        self.canvas.blit(title_surf, (w // 2 - title_surf.get_width() // 2, y))
        y += title_surf.get_height() + 26

        for s in body_surfaces:
            if s is None:
                y += 12
                continue
            self.canvas.blit(s, (w // 2 - s.get_width() // 2, y))
            y += s.get_height() + 8

        self.canvas.blit(footer_surf, (w // 2 - footer_surf.get_width() // 2, h - footer_surf.get_height() - 20))
        self._draw_version_watermark()

    # ---------------- Wi-Fiセットアップモード ----------------

    def enter_wifi_setup_mode(self):
        """ボタン長押しで呼ばれる。Piが一時的な専用アクセスポイントを立て、
        モニター/キーボード無しでもスマホだけでWi-Fi設定ができるようにする。"""
        if self.wifi_setup_active:
            return

        self.manual_active = False

        settings = load_settings(IMAGE_FOLDER, {
            "setup_ap_ssid": wifi_setup.default_setup_ssid(WIFI_SETUP_SSID_PREFIX),
            "setup_ap_password": WIFI_SETUP_DEFAULT_PASSWORD,
        })
        ssid = settings.get("setup_ap_ssid") or wifi_setup.default_setup_ssid(WIFI_SETUP_SSID_PREFIX)
        password = settings.get("setup_ap_password", WIFI_SETUP_DEFAULT_PASSWORD)

        log(f"Wi-Fiセットアップモードへ切り替え中... SSID={ssid}")
        self._hide_qr()

        ok, out, err = wifi_setup.start_hotspot(ssid, password)
        if not ok:
            log(f"アクセスポイントの起動に失敗しました: {err or out}")
            return

        self.wifi_setup_active = True
        self.wifi_setup_start_time = time.time()
        self.wifi_setup_ssid = ssid
        self.wifi_setup_password = password
        self._build_wifi_setup_surfaces()
        log("Wi-Fiセットアップモードに入りました（ボタンを押すとキャンセルできます）")

    def exit_wifi_setup_mode(self):
        """セットアップモードを明示的に終了し、アクセスポイントを閉じて通常運用に戻る"""
        if not self.wifi_setup_active:
            return
        log("Wi-Fiセットアップモードを終了しています...")
        wifi_setup.stop_hotspot()
        self.wifi_setup_active = False
        log("通常モードに戻りました")

    def _make_qr_surface(self, payload, size):
        """任意の文字列からQRコードのSurfaceを生成する共通ヘルパー"""
        qr_img = qrcode.QRCode(box_size=8, border=2)
        qr_img.add_data(payload)
        qr_img.make(fit=True)
        pil_img = qr_img.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        surface = pygame.image.load(buf)
        return pygame.transform.scale(surface, (size, size))

    def _build_wifi_setup_surfaces(self):
        """セットアップ画面に表示する2つのQRコード（①直接Wi-Fiに繋がるQRコード、
        ②設定ページを開くQRコード）と案内テキストを生成する"""
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        setup_url = f"http://{wifi_setup.get_hotspot_ip()}:{UPLOAD_PORT}/wifi"

        # 2つを横に並べても画面幅に収まるよう、幅から逆算してサイズを決める
        gap = 16
        qr_size = min((w - 48 - gap) // 2, int(h * 0.28))
        qr_size = max(qr_size, 70)

        wifi_payload = wifi_setup.wifi_qr_payload(self.wifi_setup_ssid, self.wifi_setup_password)
        self.wifi_setup_qr_wifi = self._make_qr_surface(wifi_payload, qr_size)
        self.wifi_setup_qr_url = self._make_qr_surface(setup_url, qr_size)

        label_max_width = qr_size + 20
        self.wifi_setup_qr_labels = [
            self._render_fit_text("①Wi-Fiに接続", label_max_width, start_size=18, min_size=10,
                                   color=(200, 200, 200)),
            self._render_fit_text("②設定ページを開く", label_max_width, start_size=18, min_size=10,
                                   color=(200, 200, 200)),
        ]

        max_text_width = w - 48
        lines = [
            ("Wi-Fiセットアップモード", 28, (255, 255, 255)),
            (f"SSID: {self.wifi_setup_ssid}", 22, (230, 230, 230)),
            (f"パスワード: {self.wifi_setup_password}", 22, (230, 230, 230)),
            (setup_url, 18, (150, 220, 150)),
            ("（ボタンでキャンセル）", 18, (200, 200, 200)),
        ]
        self.wifi_setup_text_surfaces = [
            self._render_fit_text(text, max_text_width, start_size=size, min_size=12, color=color)
            for text, size, color in lines
        ]

    def draw_wifi_setup_screen(self):
        """Wi-Fiセットアップモード中の画面を描画する（①②2つのQRコードを並べて表示）"""
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        self.canvas.fill((20, 20, 25))

        qr1 = self.wifi_setup_qr_wifi
        qr2 = self.wifi_setup_qr_url
        if qr1 is None or qr2 is None:
            return

        label1, label2 = self.wifi_setup_qr_labels
        label_h = max(label1.get_height(), label2.get_height())
        qr_gap = 16

        total_text_h = sum(s.get_height() + 8 for s in self.wifi_setup_text_surfaces)
        total_h = qr1.get_height() + 10 + label_h + 20 + total_text_h
        top = max(10, h // 2 - total_h // 2)

        pair_width = qr1.get_width() + qr_gap + qr2.get_width()
        pair_x = w // 2 - pair_width // 2
        qr_y = top

        for qr, x in ((qr1, pair_x), (qr2, pair_x + qr1.get_width() + qr_gap)):
            white_bg = pygame.Surface((qr.get_width() + 20, qr.get_height() + 20))
            white_bg.fill((255, 255, 255))
            self.canvas.blit(white_bg, (x - 10, qr_y - 10))
            self.canvas.blit(qr, (x, qr_y))

        label_y = qr_y + qr1.get_height() + 10
        self.canvas.blit(label1, (pair_x + qr1.get_width() // 2 - label1.get_width() // 2, label_y))
        self.canvas.blit(label2, (
            pair_x + qr1.get_width() + qr_gap + qr2.get_width() // 2 - label2.get_width() // 2, label_y))

        y = label_y + label_h + 20
        for surf in self.wifi_setup_text_surfaces:
            self.canvas.blit(surf, (w // 2 - surf.get_width() // 2, y))
            y += surf.get_height() + 8

        self._draw_version_watermark()

    # ---------------- 描画 ----------------

    def draw_pop_mode(self):
        with self._lock:
            images = self.pop_images
            cur_idx = self.current_pop_index
            next_idx = self.next_pop_index

        if not images:
            # 写真が1枚も無い（＝購入直後の無垢な状態）場合は、
            # 「画像がありません」ではなく取扱説明を自動的にループ表示する
            self.draw_manual_screen()
            return

        if self.qr_active:
            # QR表示中はスライドショーを一時停止する。時間の巻き戻しは
            # show_qr_code/_hide_qr側で行うので、ここでは現在の画像を
            # そのまま静止表示するだけでよい（切り替え判定は一切行わない）。
            self.canvas.blit(images[cur_idx % len(images)], (0, 0))
            return

        now = time.time()
        elapsed = now - self.pop_start_time

        if elapsed >= IMAGE_INTERVAL and not self.in_transition and len(images) > 1:
            self.in_transition = True
            self.next_pop_index = (cur_idx + 1) % len(images)
            next_idx = self.next_pop_index
            self.transition_start_time = now

        if self.in_transition:
            # 実フレームレートに関わらず、指定した秒数ちょうどで切り替わるよう
            # 実経過時間を基準にprogressを計算する（フレーム数基準だと、
            # 非力な機器で実際のFPSが落ちた時に想定より大幅に長くなってしまうため）
            trans_elapsed = now - self.transition_start_time
            progress = min(trans_elapsed / TRANSITION_DURATION, 1.0)

            self._draw_transition_frame(
                images[cur_idx % len(images)], images[next_idx % len(images)], progress)

            if progress >= 1.0:
                self.current_pop_index = next_idx
                self.pop_start_time = now
                self.in_transition = False
        else:
            self.canvas.blit(images[cur_idx % len(images)], (0, 0))

    def _draw_transition_frame(self, current_img, next_img, progress):
        """TRANSITION_TYPEに応じた切り替え効果を1フレーム分描画する。
        fade      : じわっと重なるクロスフェード
        slide_left : スマホのスワイプのように右→左へスライド
        slide_right: 左→右へスライド
        slide_up   : 下→上へスライド
        slide_down : 上→下へスライド
        いずれもSurfaceのコピーを作らず位置指定のblitだけで実現しているので、
        Pi Zero 2Wのような非力な機器でも軽い。"""
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        ttype = TRANSITION_TYPE

        if ttype == "slide_left":
            offset = int(w * progress)
            self.canvas.blit(current_img, (-offset, 0))
            self.canvas.blit(next_img, (w - offset, 0))
        elif ttype == "slide_right":
            offset = int(w * progress)
            self.canvas.blit(current_img, (offset, 0))
            self.canvas.blit(next_img, (offset - w, 0))
        elif ttype == "slide_up":
            offset = int(h * progress)
            self.canvas.blit(current_img, (0, -offset))
            self.canvas.blit(next_img, (0, h - offset))
        elif ttype == "slide_down":
            offset = int(h * progress)
            self.canvas.blit(current_img, (0, offset))
            self.canvas.blit(next_img, (0, offset - h))
        else:  # "fade"（未知の値が来た場合もここにフォールバック）
            alpha = int(255 * (1 - progress))
            self.canvas.blit(next_img, (0, 0))
            current_img.set_alpha(alpha)
            self.canvas.blit(current_img, (0, 0))
            current_img.set_alpha(255)  # 次回の通常表示に備えて不透明に戻す

    def run(self):
        log(f"KitchenCar POP Signage v{__version__} 起動")
        try:
            while True:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        pygame.quit()
                        sys.exit()
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            pygame.quit()
                            sys.exit()
                        if event.key == pygame.K_q:
                            # Mac等、GPIOボタンが無い環境での動作確認用
                            self.toggle_qr_code()
                        if event.key == pygame.K_m:
                            # Mac等、GPIOボタンが無い環境での取扱説明確認用
                            self.toggle_manual()
                        if event.key == pygame.K_w:
                            # Mac等、GPIOボタンが無い環境でのWi-Fiセットアップモード確認用
                            if self.wifi_setup_active:
                                self.exit_wifi_setup_mode()
                            else:
                                self.enter_wifi_setup_mode()
                        if event.key == pygame.K_s:
                            # Mac等、GPIOボタンが無い環境でのシャットダウン確認用
                            self._trigger_button_shutdown()

                self._poll_button()

                now = time.time()

                # 表示/非表示の切替と、Web側で変更された表示設定は、状態ファイルの
                # 更新時刻だけを軽くチェックして即座に反映する
                if now - self.last_hidden_check_time >= HIDDEN_CHECK_INTERVAL:
                    self.last_hidden_check_time = now

                    current_mtime = hidden_mtime(IMAGE_FOLDER)
                    if current_mtime != self.last_hidden_mtime:
                        self.last_hidden_mtime = current_mtime
                        self.load_pop_images()
                        self.last_scan_time = now
                        # スマホ側で表示/非表示の操作が行われた=もう見ているはずなので、
                        # QRコードは役目を終えたとみなして消す
                        self._hide_qr()

                    current_settings_mtime = settings_mtime(IMAGE_FOLDER)
                    if current_settings_mtime != self.last_settings_mtime:
                        self.last_settings_mtime = current_settings_mtime
                        self._apply_settings()
                        # 同様に、設定変更が行われた=スマホ操作が始まっている合図としてQRを消す
                        self._hide_qr()

                if now - self.last_scan_time >= RESCAN_INTERVAL:
                    self.last_scan_time = now
                    self.load_pop_images()

                if self.wifi_setup_active:
                    # 2秒おきに、外部(Web側の接続操作)によってアクセスポイントが
                    # 既に落とされていないか・タイムアウトしていないかを確認する
                    if now - self.last_wifi_setup_check_time >= 2:
                        self.last_wifi_setup_check_time = now
                        if not wifi_setup.is_hotspot_active():
                            log("Wi-Fi接続が完了したようです。通常モードに戻ります")
                            self.wifi_setup_active = False
                        elif now - self.wifi_setup_start_time >= WIFI_SETUP_TIMEOUT_SECONDS:
                            log("Wi-Fiセットアップモードがタイムアウトしました。通常モードに戻ります")
                            self.exit_wifi_setup_mode()

                if self.wifi_setup_active:
                    self.draw_wifi_setup_screen()
                elif self.manual_active:
                    self.draw_manual_screen()
                else:
                    self.draw_pop_mode()
                    if self.qr_active:
                        self.draw_qr_overlay()
                    self.draw_button_hold_overlay()

                if ROTATE_SCREEN:
                    # pygame.transform.rotateは反時計回りが正の角度なので、
                    # 「時計回りにROTATE_SCREEN度」は -ROTATE_SCREEN を渡す
                    rotated = pygame.transform.rotate(self.canvas, -ROTATE_SCREEN)
                    self.screen.blit(rotated, (0, 0))
                else:
                    self.screen.blit(self.canvas, (0, 0))

                pygame.display.flip()
                self.clock.tick(FPS)
        except KeyboardInterrupt:
            log("Ctrl+Cを検知、終了します")
            pygame.quit()
            sys.exit(0)


if __name__ == "__main__":
    if "--version" in sys.argv or "-v" in sys.argv:
        print(f"KitchenCar POP Signage v{__version__}")
        sys.exit(0)
    app = PopSignage()
    app.run()
