# ============================================
# KitchenCar POP Signage - main.py
# Version: 3.0.0
# 方針転換: QR決済表示をやめ、POP画像スライドショー専用の
#           ポータブルデジタルサイネージに変更
# ============================================

import os
os.environ["SDL_VIDEO_WINDOW_POS"] = "100,100"
import pygame
import sys
import random
import time
import io
import threading
from datetime import datetime
from config import *
from signage_state import load_hidden

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

        pygame.display.set_caption("KitchenCar POP Signage")
        self.clock = pygame.time.Clock()
        self.font_medium = get_japanese_font(36)
        self.font_small = get_japanese_font(24)

        self.pop_images = []          # 表示用にスケール済みSurfaceのリスト
        self._image_mtimes = {}       # ファイル名 -> mtime（再スキャン判定用）
        self.current_pop_index = 0
        self.next_pop_index = 0
        self.pop_start_time = time.time()
        self.transition_alpha = 255
        self.in_transition = False
        self.last_scan_time = 0

        self.qr_active = False        # QRコードオーバーレイの表示中フラグ
        self.qr_hide_time = 0
        self.qr_surface = None
        self.qr_label_surface = None
        self.qr_url_surface = None
        self.qr_url = ""

        self._lock = threading.Lock()
        self.load_pop_images(initial=True)

        if UPLOAD_ENABLED:
            self.start_upload_server()

        if QR_BUTTON_ENABLED:
            self.setup_qr_button()

    # ---------------- 画像読み込み ----------------

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
        new_images = []
        for f in files:
            path = os.path.join(IMAGE_FOLDER, f)
            try:
                img = pygame.image.load(path).convert()
                img = self._fit_image(img, w, h)
                new_images.append(img)
            except Exception as e:
                log(f"画像読み込みエラー: {f} - {e}")

        with self._lock:
            self.pop_images = new_images
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
        cover  : 画面いっぱいに敷き詰め、はみ出た部分はトリミングする"""
        iw, ih = img.get_size()
        canvas = pygame.Surface((w, h))
        canvas.fill(BG_COLOR)

        if IMAGE_FIT_MODE == "cover":
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
        """外付けボタンを押すとアップロードページのQRコードを表示する。
        ラズパイ実機ではGPIOボタン、Mac等GPIOが無い環境ではQキーで代用する。"""
        try:
            from gpiozero import Button
            button = Button(QR_BUTTON_GPIO_PIN, pull_up=True, bounce_time=0.2)
            button.when_pressed = self.show_qr_code
            self._qr_button = button  # ガベージコレクトされないよう保持
            log(f"QRボタン待受け開始（GPIO{QR_BUTTON_GPIO_PIN}）")
        except Exception as e:
            log(f"GPIOボタンが利用できません（{e}）。代わりにキーボードの[Q]キーでQR表示できます")

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
            log(f"QRコード表示: {url}")
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
            self.qr_active = False
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

    # ---------------- 描画 ----------------

    def draw_pop_mode(self):
        with self._lock:
            images = self.pop_images
            cur_idx = self.current_pop_index
            next_idx = self.next_pop_index

        if not images:
            self.canvas.fill((30, 30, 30))
            text = self.font_medium.render("画像がありません", True, (255, 255, 255))
            self.canvas.blit(text, (
                self.canvas.get_width() // 2 - text.get_width() // 2,
                self.canvas.get_height() // 2
            ))
            return

        now = time.time()
        elapsed = now - self.pop_start_time

        if elapsed >= IMAGE_INTERVAL and not self.in_transition and len(images) > 1:
            self.in_transition = True
            self.next_pop_index = (cur_idx + 1) % len(images)
            next_idx = self.next_pop_index
            self.transition_alpha = 255

        if self.in_transition:
            self.transition_alpha -= (255 / (TRANSITION_DURATION * FPS))
            self.canvas.blit(images[next_idx % len(images)], (0, 0))
            fading_out = images[cur_idx % len(images)].copy()
            fading_out.set_alpha(max(0, int(self.transition_alpha)))
            self.canvas.blit(fading_out, (0, 0))
            if self.transition_alpha <= 0:
                self.transition_alpha = 255
                self.current_pop_index = next_idx
                self.pop_start_time = time.time()
                self.in_transition = False
        else:
            self.canvas.blit(images[cur_idx % len(images)], (0, 0))

    def run(self):
        log("KitchenCar POP Signage 起動")
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
                            self.show_qr_code()

                now = time.time()
                if now - self.last_scan_time >= RESCAN_INTERVAL:
                    self.last_scan_time = now
                    self.load_pop_images()

                self.draw_pop_mode()
                if self.qr_active:
                    self.draw_qr_overlay()

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
    app = PopSignage()
    app.run()
