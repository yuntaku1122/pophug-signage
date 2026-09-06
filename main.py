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
import socket
import threading
from datetime import datetime
from config import *
from signage_state import (load_hidden, hidden_mtime, load_settings, settings_mtime,
                            load_priority, PRIORITY_TAGS, load_notice, notice_mtime,
                            load_pinned, save_hidden, network_recheck_mtime,
                            load_usb_update_pending, usb_update_pending_mtime,
                            clear_usb_update_pending, load_export_key_info,
                            save_export_standby, load_export_standby,
                            clear_export_standby, export_standby_mtime)
from version import __version__
import wifi_setup
import sd_watchdog
import update_check
from manual import MANUAL_PAGES

try:
    import qrcode
except ImportError:
    qrcode = None

def log(msg):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")

_font_path_cache = {"path": None, "searched": False}

# サイズごとに作成済みのFontオブジェクトを使い回すキャッシュ。
# pygame.font.Font(path, size)はフォントファイル（日本語フォントは
# 数十MBになることがある）からサイズごとにグリフを構築する重い処理のため、
# 毎回作り直すと（特に取扱説明画面のように毎フレーム呼ばれる場面で）
# 非力なPi Zero 2WのCPUを不必要に占有してしまう。一度作ったサイズは
# 使い回すことで、実質「同じサイズなら初回だけ」のコストに抑える。
_font_object_cache = {}

def get_japanese_font(size):
    if size in _font_object_cache:
        return _font_object_cache[size]

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
    font = None
    if path:
        try:
            font = pygame.font.Font(path, size)
        except Exception:
            font = None
    if font is None:
        font = pygame.font.SysFont(None, size)

    _font_object_cache[size] = font
    return font


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

        self.ordered_files = []       # 表示順のファイル名リスト（優先表示の割り込み込み）。
                                       # Surface本体ではなくファイル名だけを持つ軽量なリストで、
                                       # 実際の画像データはpinned/windowキャッシュから都度取得する
        self._pinned_cache = {}       # 固定表示ファイル名 -> スケール済みSurface（常駐。表示対象で
                                       # ある限り破棄しない）
        self._window_cache = {}       # 通常ファイル名 -> スケール済みSurface（表示中+数枚先の分だけ
                                       # 保持。範囲外に出たものは_ensure_window_cached()が破棄する）
        self._broken_files = set()    # デコードに失敗したファイル名（無限に再試行しないための記録。
                                       # 次回のフォルダ再スキャンで対象から自動的に除外される）
        self._image_mtimes = {}       # ファイル名 -> mtime（デコードキャッシュの再利用判定用）
        self._image_state_key = None  # (mtimes, 優先タグ, 割り込み間隔) の組。変化検知用
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

        # Web側の操作結果をサイネージ画面に一時表示するための状態。
        # 起動時点で既にファイルが存在していても、それは前回までの古い通知なので
        # 表示はせず、あくまで「起動後に新しく書き込まれた」ものだけを表示する。
        self.last_notice_mtime = notice_mtime(IMAGE_FOLDER)
        self.notice_text = None
        self.notice_hide_time = 0
        self.notice_lines = []  # 通知バナーの各行のSurface（折り返し済み）

        # USBメモリーに読み込める画像・アップデートパッケージ・設定ファイルが
        # 何も無かった場合の専有画面（USBメモリーが抜かれるまで表示し続ける）。
        # 小さなバナー通知とは別の独立した状態として持ち、NOTICE_STICKY_MAX_SECONDS
        # のような時間上限を設けない（抜くまで＝clear_notice()が呼ばれるまで消えない）。
        self._fullscreen_notice_active = False
        self._fullscreen_notice_message = ""

        self.qr_active = False        # QRコードオーバーレイの表示中フラグ
        self.qr_hide_time = 0
        self.qr_surface = None
        self.qr_label_surface = None
        self.qr_url_surface = None
        self.qr_url = ""
        self.qr_status_surface = None    # 接続中のSSID/IPの表示用
        self.qr_ip_surface = None        # IPアドレス版QR（.localが引けない環境向けの保険）
        self.qr_ip_label_surface = None
        self.qr_ip_url_surface = None
        self._qr_pause_start = None   # QR表示開始時刻（スライドショー一時停止分の巻き戻しに使う）

        self.manual_active = False    # ボタン長押しで手動表示中かどうか
        self.manual_page_index = 0
        self.manual_page_start_time = 0
        # 取扱説明ページのレンダリング結果（タイトル/本文/フッターのSurface）を
        # キャッシュする。ページ内容はMANUAL_PAGE_SECONDSごとにしか変わらないため、
        # 毎フレーム文字を描き直す必要は無い（キャッシュキーが変わった時だけ再描画する）
        self._manual_render_cache = {"key": None}
        # 画面右下のバージョン表示（ホスト名・バージョン番号）は実行中は変化しないため、
        # 一度だけ描画してSurfaceを使い回す
        self._watermark_surface_cache = None

        self.wifi_setup_active = False   # 「接続情報の画面」を今表示しているか
        self.wifi_setup_start_time = 0
        self.wifi_setup_ssid = ""
        self.wifi_setup_password = ""
        self.wifi_setup_qr_wifi = None     # ①: 直接Wi-Fiに繋がるQR
        self.wifi_setup_qr_url = None      # ②: 設定ページを開くQR
        self.wifi_setup_qr_labels = []
        self.wifi_setup_text_surfaces = []
        self.last_wifi_setup_check_time = 0

        self.standalone_active = False   # 自分専用APを常時ホストしているか（画面には出ない裏方の状態）
        # 起動直後は既知のWi-Fiへの接続を試す猶予を置いてから最初の判定を行う
        self.last_standalone_check_time = time.time() - STANDALONE_CHECK_INTERVAL + STANDALONE_BOOT_GRACE_SECONDS
        # Web側で保存済みWi-Fi情報を削除した等の「即時再判定要求」の検知用。
        # 起動時点で既にファイルが存在していても前回までの古い要求なので無視する
        self.last_network_recheck_mtime = network_recheck_mtime(IMAGE_FOLDER)

        # USBオフラインアップデート：保留中（本体ボタンの長押しでの確定待ち）の
        # パッケージ情報。ネットワーク再判定要求と違い、こちらは「起動時点で
        # 既にファイルが存在していれば、それも尊重して読み込む」。前回起動中に
        # USBでパッケージを検出したものの、確定する前に再起動されたケースでも
        # 確定待ちの状態を引き継げるようにするため。
        self.usb_update_pending = load_usb_update_pending(IMAGE_FOLDER)
        self.last_usb_update_pending_mtime = usb_update_pending_mtime(IMAGE_FOLDER)
        self._usb_update_applying = False  # 適用処理の多重起動防止

        self._shutting_down = False  # ボタン長押しでシャットダウンが確定した後、真になる

        # USB書き出し（吸い出し）機能：ボタン長押しでの待受けモード。
        # 起動時点で待受けファイルが残っていても（前回起動中に開始して
        # 確定しないまま再起動された等）、今回の起動では引き継がず一旦解除する。
        # main.py（このプロセス）のself.export_standby_activeが唯一の
        # 信頼できる「今まさに待受け中か」の情報源であるべきで、プロセス再起動を
        # またいで古いファイルの期限内であるというだけの理由で
        # pophug-usb-importが誤って書き出しを行ってしまうのを防ぐため。
        clear_export_standby(IMAGE_FOLDER)
        self.export_standby_active = False
        self.export_standby_start_time = 0
        self.last_export_standby_mtime = export_standby_mtime(IMAGE_FOLDER)

        self.load_pop_images(initial=True)

        if UPLOAD_ENABLED:
            self.start_upload_server()

        if QR_BUTTON_ENABLED:
            self.setup_qr_button()

        # systemdのWatchdog機能向け。起動処理がここまで完了したことを知らせ、
        # 以降はrun()のメインループ内で一定間隔ごとに生存通知を送り続ける。
        # NOTIFY_SOCKETが無い環境（Mac開発時など）では何も起きない。
        self._watchdog_interval = sd_watchdog.watchdog_interval_seconds()
        self._last_watchdog_time = 0
        sd_watchdog.notify_ready()

    # ---------------- 画像読み込み ----------------

    def _apply_settings(self):
        """images/.settings.json の内容を読み込み、実行中の設定（TRANSITION_DURATION・
        IMAGE_INTERVAL・ROTATE_SCREENなど）に反映する。Web側の操作で変更された値をここで取り込む。"""
        settings = load_settings(IMAGE_FOLDER, {
            "transition_duration": TRANSITION_DURATION,
            "image_interval": IMAGE_INTERVAL,
            "transition_type": TRANSITION_TYPE,
            "rotation": ROTATE_SCREEN,
            "image_fit_mode": IMAGE_FIT_MODE,
            "image_prefetch_window": IMAGE_PREFETCH_WINDOW,
        })

        new_prefetch_window = settings.get("image_prefetch_window", IMAGE_PREFETCH_WINDOW)
        if new_prefetch_window != globals().get("IMAGE_PREFETCH_WINDOW"):
            globals()["IMAGE_PREFETCH_WINDOW"] = new_prefetch_window
            log(f"先読み枚数を更新: {new_prefetch_window}枚"
                f"（固定表示は対象外。常駐メモリーは固定表示分+この枚数でほぼ頭打ちになる）")

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

        new_fit_mode = settings.get("image_fit_mode", IMAGE_FIT_MODE)
        if new_fit_mode != globals().get("IMAGE_FIT_MODE"):
            globals()["IMAGE_FIT_MODE"] = new_fit_mode
            log(f"画像の表示方式を更新: {new_fit_mode}")
            # _fit_image()の結果（余白の有無・トリミング範囲）は画像ごとにキャッシュ
            # されているため、表示方式が変わった場合は全て破棄して再デコードさせる
            # （ファイル自体は変わっていないので、mtimeベースの再利用ロジックだけでは
            # 古い表示方式のキャッシュが使われ続けてしまう）
            with self._lock:
                self._pinned_cache = {}
                self._window_cache = {}
                self._image_mtimes = {}
                self._image_state_key = None
                self._broken_files = set()
            self.load_pop_images(initial=True)

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
            self._pinned_cache = {}
            self._window_cache = {}
            self._image_mtimes = {}
            self._image_state_key = None
            self._broken_files = set()
        self.load_pop_images(initial=True)

    @staticmethod
    def _build_ordered_files(files, priority_map, interval):
        """通常画像と優先表示画像を組み合わせた表示順序のファイル名リストを作る。
        通常画像をinterval枚表示するごとに、優先表示1→2→3→4→5の順でまとめて
        割り込ませる。優先表示に設定された画像は、通常のローテーションからは
        除外される。同じ優先度内では、files（ファイル名の昇順）の順序を維持する。"""
        priority_tags = PRIORITY_TAGS
        normal = [f for f in files if priority_map.get(f) not in priority_tags]
        priority = []
        for tag in priority_tags:
            priority.extend(f for f in files if priority_map.get(f) == tag)

        if not priority or interval <= 0:
            return normal
        if not normal:
            return priority

        ordered = []
        for i, f in enumerate(normal):
            ordered.append(f)
            if (i + 1) % interval == 0:
                ordered.extend(priority)
        if len(normal) % interval != 0:
            # 通常画像の枚数がintervalで割り切れない場合、末尾にも一度差し込んでおく
            # （優先表示が一度も出ないまま1周してしまうのを防ぐため）
            ordered.extend(priority)
        return ordered

    def load_pop_images(self, initial=False):
        """images/ フォルダを読み込み、新しい画像や優先表示設定の変更があれば反映する。
        アップロードサーバーから随時追加される画像を検知するため定期的に呼ばれる。

        【2026-09の実機トラブルを受けての変更】以前はここで表示対象の全画像を
        まとめてデコード・スケーリングして常駐メモリーに保持していたが、画像の
        枚数・解像度によってはPi Zero 2W（RAM 512MB）のメモリを圧迫する要因に
        なり得ることが分かった。表示順（ordered_files）は一覧として確定できる
        ので、実際のSurfaceデータは「固定表示（📌）は常に全件常駐」「それ以外は
        表示中+数枚先までのウィンドウだけ保持」という方式に変更し、常駐メモリーが
        画像の総枚数ではなく固定表示分+ウィンドウ幅でほぼ頭打ちになるようにした。
        実際の先読み・不要分の破棄は_ensure_window_cached()が毎フレーム少しずつ行う
        （ここでは表示順の確定と、固定表示分のキャッシュ更新だけを行う）。"""
        supported = ('.jpg', '.jpeg', '.png')
        if not os.path.exists(IMAGE_FOLDER):
            os.makedirs(IMAGE_FOLDER)

        files = sorted(f for f in os.listdir(IMAGE_FOLDER) if f.lower().endswith(supported))
        hidden = load_hidden(IMAGE_FOLDER)
        files = [f for f in files if f not in hidden and f not in self._broken_files]
        mtimes = {f: os.path.getmtime(os.path.join(IMAGE_FOLDER, f)) for f in files}

        priority_map = load_priority(IMAGE_FOLDER)
        settings = load_settings(IMAGE_FOLDER, {"priority_interval": PRIORITY_INTERVAL})
        try:
            interval = int(settings.get("priority_interval", PRIORITY_INTERVAL))
        except (TypeError, ValueError):
            interval = PRIORITY_INTERVAL

        pinned = load_pinned(IMAGE_FOLDER) & set(files)

        # 変化検知: ファイルの追加/削除/更新だけでなく、優先表示タグ・割り込み間隔・
        # 固定表示状態の変更でも表示順序や常駐キャッシュの再構築が必要なため、
        # それらもキーに含める
        state_key = (
            tuple(sorted(mtimes.items())),
            tuple(sorted((k, v) for k, v in priority_map.items() if k in mtimes)),
            interval,
            tuple(sorted(pinned)),
        )
        if state_key == self._image_state_key:
            return  # 変化なし

        ordered_files = self._build_ordered_files(files, priority_map, interval)

        new_pinned_cache = {}
        for f in pinned:
            if f in self._pinned_cache and self._image_mtimes.get(f) == mtimes.get(f):
                # ファイル自体は変わっていない（既に固定表示だった）ので再デコードしない
                new_pinned_cache[f] = self._pinned_cache[f]
            else:
                surf = self._decode_and_fit(f)
                if surf is not None:
                    new_pinned_cache[f] = surf
                else:
                    self._broken_files.add(f)

        with self._lock:
            self._pinned_cache = new_pinned_cache
            # ウィンドウキャッシュは、固定表示に昇格した・削除された・内容が
            # 更新されたファイルだけをここで掃除する。実際の先読み（表示中+
            # 数枚先のデコード）はまとめて行わず_ensure_window_cached()に委ねる
            # （これが「全画像を常駐させない」ための肝の部分）。
            self._window_cache = {
                f: surf for f, surf in self._window_cache.items()
                if f in mtimes and f not in pinned and self._image_mtimes.get(f) == mtimes.get(f)
            }
            self.ordered_files = ordered_files
            self._image_mtimes = mtimes
            self._image_state_key = state_key
            if self.current_pop_index >= len(self.ordered_files):
                self.current_pop_index = 0
            self.next_pop_index = self.current_pop_index

        if not initial:
            priority_count = len([f for f in files if f in priority_map])
            log(f"画像フォルダを再スキャン: {len(ordered_files)}枚表示（うち優先表示 {priority_count}枚、"
                f"{interval}枚ごとに割り込み、固定表示 {len(pinned)}枚は常駐キャッシュ）")

    def _decode_and_fit(self, filename):
        """images/内の指定ファイルをデコードし、現在のキャンバスサイズに合わせて
        スケーリングしたSurfaceを返す。失敗時はNoneを返し、ログに理由を残す
        （呼び出し側はこれをbroken_filesに記録し、以後の表示対象から除外する）。"""
        path = os.path.join(IMAGE_FOLDER, filename)
        try:
            img = pygame.image.load(path).convert()
        except Exception as e:
            log(f"画像読み込みエラー: {filename} - {e}")
            return None
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        return self._fit_image(img, w, h)

    def _get_surface(self, filename):
        """指定ファイルの表示用Surfaceを返す。固定表示キャッシュ・ウィンドウ
        キャッシュのどちらにも既に無い場合（起動直後でまだ先読みが追いついて
        いない等）は、その場でデコードしてウィンドウキャッシュに追加してから
        返す（表示が欠けることは無いが、この時だけ軽い処理落ちの可能性がある）。
        デコードに失敗した場合はNoneを返し、当該ファイルを表示順から除外する
        （次回のフォルダ再スキャンを待たず、その場で表示対象から外す）。"""
        with self._lock:
            surf = self._pinned_cache.get(filename)
            if surf is None:
                surf = self._window_cache.get(filename)
        if surf is not None:
            return surf

        surf = self._decode_and_fit(filename)
        with self._lock:
            if surf is not None:
                self._window_cache[filename] = surf
            else:
                self._broken_files.add(filename)
                if filename in self.ordered_files:
                    self.ordered_files = [f for f in self.ordered_files if f != filename]
                    if self.current_pop_index >= len(self.ordered_files):
                        self.current_pop_index = 0
                    self.next_pop_index = self.current_pop_index
        return surf

    def _ensure_window_cached(self):
        """現在の表示位置からIMAGE_PREFETCH_WINDOW枚先までの通常画像（固定表示を
        除く）を先読みする。1回の呼び出しにつき最大1枚だけデコードする（まとめて
        デコードすると、そのフレームだけ処理落ちする可能性があるため意図的に
        小分けにしている）。ウィンドウの外に出た画像は破棄し、常駐メモリーが
        画像の総枚数ではなくウィンドウ幅でほぼ頭打ちになるようにする
        （固定表示分は対象外。常に別枠で保持され続ける）。

        表示順（ordered_files）には固定表示画像も通常の並びの中に混在している
        ため、単純に「現在位置からwindow_size件」を切り出すと、その中に固定
        表示画像が含まれる分だけ通常画像の実質的な先読み枚数が減ってしまう
        （固定表示は既に別キャッシュで常駐済みなので、ここでは無視して読み飛ばし、
        その分さらに先まで見て通常画像をwindow_size枚ちょうど集める）。"""
        with self._lock:
            ordered = list(self.ordered_files)
            cur_idx = self.current_pop_index
            pinned_keys = set(self._pinned_cache.keys())

        n = len(ordered)
        if n == 0:
            return

        window_size = min(IMAGE_PREFETCH_WINDOW, n)
        desired_normal = []
        seen = set()
        for i in range(n):  # 最大1周分だけ辿る（全件固定表示等の極端な場合でも無限ループしない）
            f = ordered[(cur_idx + i) % n]
            if f in pinned_keys or f in seen:
                continue
            seen.add(f)
            desired_normal.append(f)
            if len(desired_normal) >= window_size:
                break
        desired_set = set(desired_normal)

        with self._lock:
            # ウィンドウ外に出た通常画像はここで解放する
            self._window_cache = {
                f: surf for f, surf in self._window_cache.items() if f in desired_set
            }
            missing = [f for f in desired_normal if f not in self._window_cache]

        if not missing:
            return

        # 1フレームにつき1枚だけ先読みデコードする
        self._get_surface(missing[0])

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
        """ボタン1つで6段階の操作を行う。
        短押し             : QRコード表示/非表示トグル
        MANUAL_HOLD_SECONDS秒以上長押し        : 取扱説明を表示
        WIFI_SETUP_HOLD_SECONDS秒以上長押し    : Wi-Fiセットアップモード
        RESET_DISPLAY_HOLD_SECONDS秒以上長押し : 表示リセット（固定表示のみ表示）
        SHUTDOWN_HOLD_SECONDS秒以上長押し      : シャットダウン
        EXPORT_HOLD_SECONDS秒以上長押し        : USB書き出し待受けモード
        判定は「離した瞬間の合計長押し時間」で行う（押している間は毎フレーム
        run()から_poll_button()が呼ばれ、進捗を画面に表示する）。
        ラズパイ実機ではGPIOボタン、Mac等GPIOが無い環境では
        Qキー(QR表示)・Mキー(取扱説明)・Wキー(Wi-Fiセットアップモード)・
        Rキー(表示リセット)・Sキー(シャットダウン)・Eキー(USB書き出し待受け)
        で代用する。"""
        self._qr_button = None
        self._button_press_start = None
        try:
            from gpiozero import Button
            self._qr_button = Button(QR_BUTTON_GPIO_PIN, pull_up=True, bounce_time=0.2)
            log(f"QRボタン待受け開始（GPIO{QR_BUTTON_GPIO_PIN}）: "
                f"短押し=QR表示 / {MANUAL_HOLD_SECONDS}秒長押し=取扱説明 / "
                f"{WIFI_SETUP_HOLD_SECONDS}秒長押し=Wi-Fiセットアップ / "
                f"{RESET_DISPLAY_HOLD_SECONDS}秒長押し=表示リセット / "
                f"{SHUTDOWN_HOLD_SECONDS}秒長押し=シャットダウン / "
                f"{EXPORT_HOLD_SECONDS}秒長押し=USB書き出し待受け")
        except Exception as e:
            log(f"GPIOボタンが利用できません（{e}）。"
                f"代わりにキーボードの[Q]キーでQR表示、[M]キーで取扱説明、"
                f"[W]キーでWi-Fiセットアップ、[R]キーで表示リセット、"
                f"[S]キーでシャットダウン、[E]キーでUSB書き出し待受けを確認できます")

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
        if self._shutting_down:
            # シャットダウンが確定した後は、実際に電源が落ちるまでの短い間に
            # ボタンが再度押されても一切反応しない（誤操作防止）
            return

        if self.export_standby_active:
            # USB書き出し待受け中は、押した時間に関わらずキャンセル操作として扱う
            # （待受けに入るための長押しラダーとは独立させ、誤って別の操作と
            # 混同しないようにするため。他の能動的モードと同じ扱い）
            self._cancel_export_standby(reason="ボタン操作によりキャンセル")
            return

        if self.wifi_setup_active:
            # セットアップモード中は、押した時間に関わらずキャンセル操作として扱う
            self.exit_wifi_setup_mode()
            return

        if self.manual_active:
            # 取扱説明の表示中でも、SHUTDOWN_HOLD_SECONDS以上の長押しだけは
            # シャットダウンとして扱う（説明書を見ながら片付けたい場面で
            # シャットダウンできないのは不便なため）。_trigger_button_shutdown()
            # は内部でmanual_activeをFalseに戻すので、そのまま呼んでよい。
            # それ未満の押下は、従来通り押した時間に関わらず「閉じる」操作として扱う。
            if held_seconds >= SHUTDOWN_HOLD_SECONDS:
                self._trigger_button_shutdown()
            else:
                self.toggle_manual()
            return

        if self.usb_update_pending:
            # USBアップデートパッケージが確定待ちの間は、ボタンの意味を
            # 丸ごと「適用の確定/見送り」に切り替える（通常のQR表示等の
            # ラダーとは独立させ、誤って別の操作と混同しないようにするため）
            self._handle_usb_update_button(held_seconds)
            return

        if held_seconds >= EXPORT_HOLD_SECONDS:
            self._enter_export_standby()
        elif held_seconds >= SHUTDOWN_HOLD_SECONDS:
            self._trigger_button_shutdown()
        elif held_seconds >= RESET_DISPLAY_HOLD_SECONDS:
            self._reset_display_to_pinned_only()
        elif held_seconds >= WIFI_SETUP_HOLD_SECONDS:
            self.enter_wifi_setup_mode()
        elif held_seconds >= MANUAL_HOLD_SECONDS:
            self.toggle_manual()
        else:
            self.toggle_qr_code()

    def _reset_display_to_pinned_only(self):
        """ボタン長押しで、表示状態だけを初期化する（固定表示画像だけを表示状態にし、
        それ以外を全て非表示にする）。images/フォルダの画像データ自体は一切削除しない。

        次の事業者に本体を引き継ぐ場面を想定している。この操作をしておけば、
        起動直後の画面は固定表示（例：市からのお知らせ）だけになり、次の事業者が
        自分のUSBメモリーを挿すと、そのUSBの中身がそのまま表示されるようになる
        （USB取り込み時の「固定表示以外を自動非表示化」する動作と同じ状態を、
        USBを挿さずに手元のボタン操作だけで作れる）。"""
        supported = ('.jpg', '.jpeg', '.png')
        try:
            files = [f for f in os.listdir(IMAGE_FOLDER) if f.lower().endswith(supported)]
        except OSError as e:
            log(f"表示リセットに失敗しました（画像フォルダの読み込みエラー: {e}）")
            return

        pinned = load_pinned(IMAGE_FOLDER)
        shown = [f for f in files if f in pinned]
        hidden = set(f for f in files if f not in pinned)
        save_hidden(IMAGE_FOLDER, hidden)

        log(f"ボタン長押しにより表示をリセットしました"
            f"（固定表示{len(shown)}枚のみ表示、{len(hidden)}枚を非表示化。画像データは削除していません）")
        self._show_notice(f"表示をリセットしました（固定表示{len(shown)}枚のみ表示中）")

    def _handle_usb_update_button(self, held_seconds):
        """USBアップデートパッケージが確定待ちの間のボタン処理。
        USB_UPDATE_CONFIRM_HOLD_SECONDS秒以上の長押しだけを「確定・適用」とし、
        それ未満（短押しも含む）は全て「見送り（キャンセル）」として扱う。
        誤操作防止のため、確定には明確な長押しを必須にしている。"""
        pending = self.usb_update_pending
        if self._usb_update_applying:
            # 既に適用処理が進行中の間は、確定・見送りどちらの操作も受け付けない
            # （見送り操作でステージング済みzipを消してしまうと、進行中の適用処理が
            # 参照しているファイルが無くなり中途半端な状態になりかねないため）
            log("USBアップデートは適用処理中のため、ボタン操作を無視しました")
            return
        if held_seconds >= USB_UPDATE_CONFIRM_HOLD_SECONDS:
            self._confirm_usb_update(pending)
        else:
            self._dismiss_usb_update(pending, reason="ボタン短押しのため見送り")

    def _confirm_usb_update(self, pending):
        """保留中のUSBアップデートをボタン長押しで確定し、適用する。
        適用処理そのものはオンラインOTAと全く同じ検証・バックアップ・
        ロールバック経路（update_check.apply_update_from_zip）を通るため、
        安全性は同等。成功時はこの後サービスごと再起動される想定。それまでの
        数秒間は_usb_update_applyingフラグを見てrun()側がdraw_usb_update_applying_screen
        を毎フレーム描画し続けるため、途中でスライドショーや古い通知・バッジに
        画面が戻って「失敗した？」と誤解されることはない。失敗時はロールバック
        済みで元のバージョンのまま動き続けるので、エラー内容を通知して
        保留状態だけ解除する。多重起動防止は呼び出し元(_handle_usb_update_button)
        で行っている。"""
        version = pending.get("version", "?")
        zip_path = pending.get("zip_path")
        self._usb_update_applying = True
        log(f"USBアップデートの適用をボタン長押しで確定しました: v{version}")

        self._hide_qr()
        self.manual_active = False

        def do_apply():
            ok, msg = update_check.apply_update_from_zip(zip_path, version)
            if not ok:
                # 成功時はここに到達する前にサービスごと再起動される想定。
                # 到達した＝失敗（ロールバック済みで元のバージョンのまま動作継続）
                log(f"USBアップデート適用に失敗しました: {msg}")
                clear_usb_update_pending(IMAGE_FOLDER)
                self._usb_update_applying = False
                self._show_notice(f"USBアップデートの適用に失敗しました（{msg}）", sticky=True)

        threading.Thread(target=do_apply, daemon=True).start()

    def _dismiss_usb_update(self, pending, reason):
        """保留中のUSBアップデートを見送る（適用しない）。ステージング済みの
        zip本体も削除するため、SDカードの空き容量を圧迫したままにならない。
        次回同じUSBを挿し直せば、再度検出・確定待ちにできる。"""
        version = pending.get("version", "?")
        log(f"USBアップデート（v{version}）を見送りました: {reason}")
        clear_usb_update_pending(IMAGE_FOLDER)
        self.usb_update_pending = None
        self._show_notice(
            f"USBアップデート（v{version}）の適用を見送りました。"
            f"再度適用するにはUSBメモリーを挿し直してください")

    def _trigger_button_shutdown(self):
        """物理ボタンの長押しでシャットダウンを実行する（Web版と同じsudoersの許可を利用）。
        以前はここで「シャットダウンしています...」を1フレームだけ描画してflipしていたが、
        実際のシャットダウンコマンドが効くまでの1秒ほどの間にrun()側の通常描画（スライドショー）
        に上書きされてしまい、あたかもシャットダウンされなかったかのように見える不具合があった
        （USBアップデート適用中の表示と同種の問題）。_shutting_downフラグをrun()側の描画分岐に
        組み込み、電源が落ちるまで専有画面を毎フレーム描画し続けるようにしている。"""
        log("ボタン長押しによるシャットダウン要求を受け付けました")
        self._hide_qr()
        self.manual_active = False
        self._shutting_down = True

        def do_shutdown():
            time.sleep(1)  # 専有画面が確実に数フレーム表示されてから実行する
            try:
                subprocess.run(SHUTDOWN_COMMAND, check=True, capture_output=True, text=True, timeout=15)
            except Exception as e:
                log(f"シャットダウンに失敗しました: {e}")
                # コマンド自体が失敗した場合は、電源は落ちないままなので専有画面を解除し、
                # 通常表示に戻した上でエラーを知らせる（＝画面が固まって見えることを防ぐ）
                self._shutting_down = False
                self._show_notice(f"シャットダウンに失敗しました（{e}）", sticky=True)

        threading.Thread(target=do_shutdown, daemon=True).start()

    def draw_shutdown_screen(self):
        """シャットダウンが確定してから実際に電源が落ちるまでの間、毎フレーム描画する
        専有画面。draw_usb_update_applying_screenと同じ考え方で、古い画面（スライドショー等）
        が再表示されて「シャットダウンされなかった」と誤解されることのないようにしている。"""
        self.canvas.fill((10, 10, 10))
        surf = self._render_fit_text(
            "シャットダウンしています...", self.canvas.get_width() - 48,
            start_size=30, min_size=16, color=(255, 120, 120))
        sub = self._render_fit_text(
            "しばらくすると電源が切れます", self.canvas.get_width() - 48,
            start_size=18, min_size=12, color=(200, 200, 200))
        total_h = surf.get_height() + 16 + sub.get_height()
        base_y = self.canvas.get_height() // 2 - total_h // 2
        self.canvas.blit(surf, (self.canvas.get_width() // 2 - surf.get_width() // 2, base_y))
        self.canvas.blit(sub, (self.canvas.get_width() // 2 - sub.get_width() // 2,
                                base_y + surf.get_height() + 16))

    def _enter_export_standby(self):
        """ボタンの最長長押しで、USB書き出し待受けモードに入る。
        書き出し用の合言葉キーがまだ発行されていない場合は、待受けに入らず
        エラー通知だけを出す（先にWeb設定画面で発行してもらう必要があるため）。
        待受け中は images/.export_standby.json に期限付きで記録し、root権限で
        動くpophug-usb-import（別プロセス）が、USBメモリー挿入時にこれを見て
        書き出しを試みるかどうかを判断する。"""
        if self.export_standby_active:
            return

        key_info = load_export_key_info(IMAGE_FOLDER)
        if not key_info:
            log("USB書き出し待受けが要求されましたが、書き出し用キーが未発行のため中止しました")
            self._show_notice("USB書き出し用のキーが未発行です。設定画面（スマホ）から発行してください")
            return

        expires_at = time.time() + EXPORT_STANDBY_TIMEOUT_SECONDS
        save_export_standby(IMAGE_FOLDER, expires_at)
        self.last_export_standby_mtime = export_standby_mtime(IMAGE_FOLDER)
        self.export_standby_active = True
        self.export_standby_start_time = time.time()
        self._hide_qr()
        self.manual_active = False
        log(f"USB書き出し待受けモードに入りました"
            f"（{EXPORT_STANDBY_TIMEOUT_SECONDS}秒以内に合言葉入りのUSBメモリーを挿してください）")

    def _cancel_export_standby(self, reason):
        """USB書き出し待受けモードを終了する（ボタン操作・タイムアウト・
        別プロセスでの書き出し完了検知のいずれからも呼ばれる）。"""
        if not self.export_standby_active:
            return
        clear_export_standby(IMAGE_FOLDER)
        self.export_standby_active = False
        log(f"USB書き出し待受けモードを終了しました: {reason}")
        self._show_notice("USB書き出し待受けを終了しました")

    def draw_export_standby_screen(self):
        """USB書き出し待受け中に毎フレーム描画する専有画面。
        draw_shutdown_screen等と同じ考え方で、待受け中は他の画面（スライドショー等）
        に上書きされず、合言葉入りのUSBメモリーを挿すよう案内し続ける。"""
        self.canvas.fill((10, 10, 10))
        remaining = max(0.0, EXPORT_STANDBY_TIMEOUT_SECONDS - (time.time() - self.export_standby_start_time))
        surf = self._render_fit_text(
            "USB書き出し待受け中", self.canvas.get_width() - 48,
            start_size=30, min_size=16, color=(150, 220, 255))
        sub = self._render_fit_text(
            "合言葉入りのUSBメモリーを挿してください", self.canvas.get_width() - 48,
            start_size=18, min_size=12, color=(220, 220, 220))
        sub2 = self._render_fit_text(
            f"あと{remaining:.0f}秒でキャンセル（ボタンを押すと今すぐキャンセル）",
            self.canvas.get_width() - 48, start_size=14, min_size=10, color=(180, 180, 180))
        total_h = surf.get_height() + 12 + sub.get_height() + 12 + sub2.get_height()
        base_y = self.canvas.get_height() // 2 - total_h // 2
        self.canvas.blit(surf, (self.canvas.get_width() // 2 - surf.get_width() // 2, base_y))
        self.canvas.blit(sub, (self.canvas.get_width() // 2 - sub.get_width() // 2,
                                base_y + surf.get_height() + 12))
        self.canvas.blit(sub2, (self.canvas.get_width() // 2 - sub2.get_width() // 2,
                                 base_y + surf.get_height() + 12 + sub.get_height() + 12))

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

        if self.manual_active:
            # 取扱説明の表示中は、通常の5段階ラダーではなく「シャットダウンか、
            # 閉じるか」の2択だけを見せる（_handle_button_release側の分岐と対応させる）
            if held >= SHUTDOWN_HOLD_SECONDS:
                text = "離すとシャットダウンします"
                color = (255, 90, 90)
            else:
                remaining = SHUTDOWN_HOLD_SECONDS - held
                text = f"長押し中...あと{remaining:.1f}秒でシャットダウン（今離すと閉じます）"
                color = (255, 255, 255)
        elif self.usb_update_pending:
            # 保留中のUSBアップデートがある間は、通常のラダー（取扱説明～
            # シャットダウン）を丸ごと無視し、確定/見送りの進捗だけを見せる
            version = self.usb_update_pending.get("version", "?")
            if held >= USB_UPDATE_CONFIRM_HOLD_SECONDS:
                text = f"離すとアップデートを適用します（v{version}）"
                color = (150, 220, 255)
            else:
                remaining = USB_UPDATE_CONFIRM_HOLD_SECONDS - held
                text = f"長押し中...あと{remaining:.1f}秒でアップデート適用（v{version}）（今離すと見送り）"
                color = (255, 210, 120)
        elif held >= EXPORT_HOLD_SECONDS:
            text = "離すとUSB書き出し待受けモードに入ります"
            color = (150, 220, 255)
        elif held >= SHUTDOWN_HOLD_SECONDS:
            remaining = EXPORT_HOLD_SECONDS - held
            text = f"離すとシャットダウンします（あと{remaining:.0f}秒でUSB書き出し待受け）"
            color = (255, 90, 90)
        elif held >= RESET_DISPLAY_HOLD_SECONDS:
            remaining = SHUTDOWN_HOLD_SECONDS - held
            text = f"離すと表示をリセット（固定表示のみに）（あと{remaining:.0f}秒でシャットダウン）"
            color = (255, 170, 100)
        elif held >= WIFI_SETUP_HOLD_SECONDS:
            remaining = RESET_DISPLAY_HOLD_SECONDS - held
            text = f"離すとWi-Fiセットアップモード（あと{remaining:.0f}秒で表示リセット）"
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
        """QR表示中にもう一度押されたら即座に消す。非表示中なら新たに表示する。

        スタンドアロンモード（既知の外部Wi-Fiが見つからず、本体が自分専用の
        アクセスポイントを立てている状態）の時は、単純にアップロードURLのQRだけを
        見せると「スマホがそのアクセスポイントに接続していないためページが開けない」
        という混乱を招く（実際に利用者から報告があった）。この場合は短押しでも、
        Wi-Fiセットアップ画面と同じ「①まずAPに接続 → ②設定ページを開く」の
        2ステップ案内画面を表示する。"""
        if self.wifi_setup_active:
            # セットアップモード中（案内画面表示中）はボタンをキャンセル操作として扱う
            self.exit_wifi_setup_mode()
            return
        if self.qr_active:
            self._hide_qr()
            log("QRコード非表示（ボタン再押下）")
            return

        # standalone_activeフラグは定期チェック（最大STANDALONE_CHECK_INTERVAL秒間隔）
        # でしか更新されないため、Web側でWi-Fi情報を削除した直後などはフラグが
        # まだ「未接続」を反映しておらず、本来スタンドアロン用の案内画面を出す
        # べき場面で通常のQR（＝接続中である前提の画面）を誤って表示してしまう
        # ことがあった。ボタンが押されたこのタイミングで実際の接続状態を
        # 直接（read-onlyの軽い確認で）取り直し、未接続なら定期チェックを
        # 待たずその場でスタンドアロンモードへ移行してから案内画面を出す。
        if (not self.standalone_active and STANDALONE_AUTO_ENABLED
                and not wifi_setup.is_client_connected()):
            log("QR短押し時点で外部Wi-Fiへの接続が確認できないため、"
                "即座にスタンドアロンモードへの移行を試みます")
            self._enter_standalone_mode()
            self.last_standalone_check_time = time.time()

        if self.standalone_active:
            log("スタンドアロンモード中のため、QR短押しでも接続手順の案内画面を表示します")
            self.enter_wifi_setup_mode()
            return
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

    def _show_notice(self, message, sticky=False, fullscreen=False):
        """Web側の操作結果を、サイネージ画面上部にバナー表示する。
        スライドショー自体は止めない（QRのように操作を待つものではなく、
        あくまで「今こういう操作がありました」という控えめな通知のため）。
        sticky=Trueの時は「取り外すまで表示」系の通知として、通常より長く
        （NOTICE_STICKY_MAX_SECONDSを上限に）表示し続ける。
        長いメッセージ（USB取り込み完了時の詳細な内訳など）でも画面からはみ出さない
        よう、1行の大きさを固定して複数行に折り返す（取扱説明画面と同じ方式）。

        fullscreen=Trueの時は、小さなバナーではなく黒背景に大きな文字で画面全体を
        占有する専有画面として表示する（USBメモリーに読み込める内容が何も無かった
        場合など、見落とされては困る通知向け）。時間上限は設けず、USBメモリーが
        抜かれてclear_notice()が呼ばれるまで表示し続ける。"""
        self._fullscreen_notice_active = fullscreen
        self._fullscreen_notice_message = message if fullscreen else ""
        if fullscreen:
            # バナー側の状態は使わないため、念のためクリアしておく
            self.notice_lines = []
            self.notice_hide_time = 0
            log(f"サイネージ画面に全画面通知を表示: {message}")
            return

        max_width = self.canvas.get_width() - 64
        font = get_japanese_font(22)
        self.notice_lines = [
            font.render(line, True, (255, 255, 255))
            for line in self._wrap_text_lines(message, font, max_width)
        ]
        seconds = NOTICE_STICKY_MAX_SECONDS if sticky else NOTICE_DISPLAY_SECONDS
        self.notice_hide_time = time.time() + seconds
        log(f"サイネージ画面に通知を表示: {message}" + ("（sticky）" if sticky else ""))

    def _hide_notice(self):
        """表示中の通知（バナー・全画面のどちらも）を即座に消す
        （USBメモリー取り外し検知など）"""
        self.notice_lines = []
        self.notice_hide_time = 0
        self._fullscreen_notice_active = False
        self._fullscreen_notice_message = ""

    def draw_notice_overlay(self):
        if time.time() >= self.notice_hide_time or not self.notice_lines:
            return
        w = self.canvas.get_width()
        pad_x, pad_y, line_gap = 20, 12, 4
        box_w = max(s.get_width() for s in self.notice_lines) + pad_x * 2
        box_h = (sum(s.get_height() for s in self.notice_lines)
                 + line_gap * (len(self.notice_lines) - 1) + pad_y * 2)
        box_x = w // 2 - box_w // 2
        box_y = 16

        box = pygame.Surface((box_w, box_h))
        box.set_alpha(225)
        box.fill((34, 139, 34))
        self.canvas.blit(box, (box_x, box_y))

        y = box_y + pad_y
        for surf in self.notice_lines:
            self.canvas.blit(surf, (box_x + box_w // 2 - surf.get_width() // 2, y))
            y += surf.get_height() + line_gap

    def draw_fullscreen_notice(self):
        """USBメモリーに読み込める画像・アップデートパッケージ・設定ファイルが
        何も無かった場合の専有画面。黒背景に大きな文字でメッセージを表示し、
        USBメモリーが抜かれる（clear_notice()が呼ばれる）まで表示し続ける。
        小さなバナー通知(draw_notice_overlay)と違い見落とされてはならない
        通知のため、画面全体を占有する。"""
        self.canvas.fill((0, 0, 0))
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        message = self._fullscreen_notice_message or ""
        max_width = w - 80
        max_height = h - 80

        font_size = 44
        font = get_japanese_font(font_size)
        lines = self._wrap_text_lines(message, font, max_width)
        # 行数が多い（＝メッセージが長い）場合、画面の縦幅に収まるまで
        # フォントサイズを段階的に縮小する
        while font_size > 16:
            line_height = font.get_height()
            total_h = line_height * len(lines) + 14 * (len(lines) - 1)
            if total_h <= max_height:
                break
            font_size -= 2
            font = get_japanese_font(font_size)
            lines = self._wrap_text_lines(message, font, max_width)

        line_height = font.get_height()
        total_h = line_height * len(lines) + 14 * (len(lines) - 1)
        y = h // 2 - total_h // 2
        for line in lines:
            surf = font.render(line, True, (255, 215, 100))
            self.canvas.blit(surf, (w // 2 - surf.get_width() // 2, y))
            y += line_height + 14

    def draw_usb_update_badge(self):
        """USBアップデートパッケージが確定待ちの間、画面下部に控えめなバッジを
        常時表示する。通知バナー(draw_notice_overlay)と違い、確定・見送りされる
        まで表示時間の上限なく出続けるべきものなので、あえて別の独立した
        仕組みにしている。デジタルサイネージ本来の役割（スライドショー表示）を
        妨げないよう、画面を占有せず控えめな帯だけにとどめる。
        ボタン長押し中はdraw_button_hold_overlayの方で進捗を見せるので、
        二重表示を避けるためここでは何も描かない。
        適用処理が既に確定・進行中(_usb_update_applying)の間は
        draw_usb_update_applying_screenが画面を専有するので、こちらは呼ばれない
        （run()側の分岐で切り替えている）。"""
        if not self.usb_update_pending or self._button_press_start is not None:
            return

        version = self.usb_update_pending.get("version", "?")
        text = f"アップデート待機中 v{version}（本体ボタン{USB_UPDATE_CONFIRM_HOLD_SECONDS}秒長押しで適用）"
        font = get_japanese_font(18)
        surf = font.render(text, True, (30, 30, 30))

        w = self.canvas.get_width()
        pad_x, pad_y = 14, 8
        bar_h = surf.get_height() + pad_y * 2
        bar = pygame.Surface((w, bar_h))
        bar.set_alpha(225)
        bar.fill((255, 205, 80))
        bar.blit(surf, (max(0, (w - surf.get_width()) // 2), pad_y))
        self.canvas.blit(bar, (0, self.canvas.get_height() - bar_h))

    def draw_usb_update_applying_screen(self):
        """USBアップデートの適用処理が確定・進行中の間、毎フレーム描画する
        専有画面。以前はこの表示を確定した瞬間に1回だけ描画してflipしていたが、
        その直後にrun()側の通常描画（スライドショー＋「アップデート待機中」バッジ＋
        USB挿入時の通知バナー）に次のフレームで上書きされてしまい、あたかも
        アップデートが失敗したかのように見える不具合があった（実際には裏で
        適用処理が進行中だっただけ）。この関数をwifi_setup_active/manual_activeと
        同様に「専有画面」としてrun()側の分岐に組み込み、適用処理が終わる
        （＝プロセスごと再起動される）まで毎フレーム描画し続けることで、
        古い画面が再表示されて誤解を招くことがないようにしている。"""
        version = self.usb_update_pending.get("version", "?") if self.usb_update_pending else "?"
        self.canvas.fill((10, 10, 10))
        surf = self._render_fit_text(
            f"アップデートを適用しています（v{version}）...",
            self.canvas.get_width() - 48, start_size=28, min_size=14, color=(150, 220, 255))
        sub = self._render_fit_text(
            "しばらくお待ちください。自動的に再起動します（ボタン操作は不要です）",
            self.canvas.get_width() - 48, start_size=18, min_size=12, color=(200, 200, 200))
        total_h = surf.get_height() + 16 + sub.get_height()
        base_y = self.canvas.get_height() // 2 - total_h // 2
        self.canvas.blit(surf, (self.canvas.get_width() // 2 - surf.get_width() // 2, base_y))
        self.canvas.blit(sub, (self.canvas.get_width() // 2 - sub.get_width() // 2,
                                base_y + surf.get_height() + 16))

    def show_qr_code(self):
        """アップロードページのURLをQRコードとして生成し、画面に一定時間オーバーレイ表示する。

        mDNS(.local)は、ルーターによっては正しく機能せず名前解決に失敗することがある
        （現場での実運用で確認済み）。ホスト名版のQRだけでなく、その場でIPアドレス版の
        QRも一緒に表示することで、どちらかで必ずアクセスできるようにする。"""
        if qrcode is None:
            log("qrcodeライブラリが未インストールのためQR表示できません（pip install qrcode[pil]）")
            return

        # IPアドレスは接続先Wi-Fiが変わる・DHCPで再割り当てされるたびに変化するため、
        # mDNS(.local)ホスト名を主として案内する（明示的な上書き指定があればそちらを優先）。
        hostname_url = UPLOAD_URL_OVERRIDE or f"http://{socket.gethostname()}.local:{UPLOAD_PORT}"
        local_ip = self._get_local_ip()
        # UPLOAD_URL_OVERRIDEで固定運用している場合や、そもそもIPが取得できていない
        # (オフライン)場合は、IPアドレス版QRを出しても意味が無いので省略する
        ip_url = None
        if not UPLOAD_URL_OVERRIDE and local_ip and local_ip != "127.0.0.1":
            ip_url = f"http://{local_ip}:{UPLOAD_PORT}"

        try:
            def make_qr_surface(url, box_size):
                qr = qrcode.QRCode(box_size=box_size, border=2)
                qr.add_data(url)
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white")
                buf = io.BytesIO()
                qr_img.save(buf, format="PNG")
                buf.seek(0)
                return pygame.image.load(buf)

            max_text_width = self.canvas.get_width() - 48
            short_side = min(self.canvas.get_width(), self.canvas.get_height())

            # QR2つ分を表示する場合は、メインの方を少し小さめにして余白を確保する
            primary_ratio = 0.45 if ip_url is None else 0.38
            primary_size = int(short_side * primary_ratio)
            self.qr_surface = pygame.transform.scale(make_qr_surface(hostname_url, 8), (primary_size, primary_size))
            self.qr_url = hostname_url
            self.qr_label_surface = self._render_fit_text(
                "写真アップロードはこちら", max_text_width, start_size=32, min_size=16)
            self.qr_url_surface = self._render_fit_text(
                hostname_url, max_text_width, start_size=22, min_size=12, color=(210, 210, 210))

            # 接続中のWi-Fi状況（分かる範囲で）。ホスト名で開けなかった時に、
            # 手動でIPアドレスを入力する際の手がかりにもなる
            conn = wifi_setup.current_connection_info()
            if conn:
                status_text = f"接続中: {conn['ssid']}（{conn['ip']}）"
            elif ip_url:
                status_text = f"IPアドレス: {local_ip}"
            else:
                status_text = "ネットワーク状態を取得できませんでした"
            self.qr_status_surface = self._render_fit_text(
                status_text, max_text_width, start_size=18, min_size=12, color=(160, 205, 160))

            if ip_url:
                secondary_size = int(short_side * 0.22)
                self.qr_ip_surface = pygame.transform.scale(make_qr_surface(ip_url, 6), (secondary_size, secondary_size))
                self.qr_ip_label_surface = self._render_fit_text(
                    "つながらない場合はこちら（IPアドレス版）", max_text_width, start_size=18, min_size=12,
                    color=(200, 200, 200))
                self.qr_ip_url_surface = self._render_fit_text(
                    ip_url, max_text_width, start_size=18, min_size=11, color=(180, 180, 180))
            else:
                self.qr_ip_surface = None
                self.qr_ip_label_surface = None
                self.qr_ip_url_surface = None

            self.qr_active = True
            self.qr_hide_time = time.time() + QR_DISPLAY_SECONDS
            self._qr_pause_start = time.time()  # スライドショー一時停止の起点を記録
            log(f"QRコード表示: {hostname_url}"
                + (f" / IP版: {ip_url}" if ip_url else "")
                + "（スライドショーを一時停止）")
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
        status_s = self.qr_status_surface
        ip_qr = self.qr_ip_surface
        ip_label = self.qr_ip_label_surface
        ip_url_s = self.qr_ip_url_surface

        gap = 14
        total_h = qr.get_height() + gap + label.get_height() + 8 + url_s.get_height()
        if status_s:
            total_h += status_s.get_height() + gap
        if ip_qr:
            total_h += gap + 14 + ip_label.get_height() + 8 + ip_qr.get_height() + 8 + ip_url_s.get_height()

        y = max(16, h // 2 - total_h // 2)

        if status_s:
            self.canvas.blit(status_s, (w // 2 - status_s.get_width() // 2, y))
            y += status_s.get_height() + gap

        qr_x = w // 2 - qr.get_width() // 2
        white_bg = pygame.Surface((qr.get_width() + 24, qr.get_height() + 24))
        white_bg.fill((255, 255, 255))
        self.canvas.blit(white_bg, (qr_x - 12, y - 12))
        self.canvas.blit(qr, (qr_x, y))
        y += qr.get_height() + gap

        self.canvas.blit(label, (w // 2 - label.get_width() // 2, y))
        y += label.get_height() + 8

        self.canvas.blit(url_s, (w // 2 - url_s.get_width() // 2, y))
        y += url_s.get_height()

        if ip_qr:
            y += gap
            pygame.draw.line(self.canvas, (90, 90, 90), (w // 2 - 60, y), (w // 2 + 60, y), 1)
            y += 14

            self.canvas.blit(ip_label, (w // 2 - ip_label.get_width() // 2, y))
            y += ip_label.get_height() + 8

            ip_qr_x = w // 2 - ip_qr.get_width() // 2
            white_bg2 = pygame.Surface((ip_qr.get_width() + 16, ip_qr.get_height() + 16))
            white_bg2.fill((255, 255, 255))
            self.canvas.blit(white_bg2, (ip_qr_x - 8, y - 8))
            self.canvas.blit(ip_qr, (ip_qr_x, y))
            y += ip_qr.get_height() + 8

            self.canvas.blit(ip_url_s, (w // 2 - ip_url_s.get_width() // 2, y))

        self._draw_version_watermark()

    def _draw_version_watermark(self):
        """画面右下に小さくバージョン番号とホスト名を表示する（QR/取扱説明/Wi-Fiセットアップ画面用。
        Webページを開かなくても、画面を見ただけで今のバージョン・機体が分かるようにするため）。
        内容（ホスト名・バージョン）はプロセス実行中は変わらないため、初回だけ描画してSurfaceを使い回す。"""
        if self._watermark_surface_cache is None:
            hostname = socket.gethostname()
            self._watermark_surface_cache = get_japanese_font(14).render(
                f"{hostname} / v{__version__}", True, (140, 140, 140))
        surf = self._watermark_surface_cache
        w = self.canvas.get_width()
        h = self.canvas.get_height()
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
        （写真が1枚も無い時の自動表示、ボタン長押しでの手動表示、両方から呼ばれる）
        ページ内容（タイトル・本文・フッター）のレンダリング結果はキャッシュし、
        ページが切り替わった時（またはキャンバスサイズが変わった時）だけ再描画する。
        毎フレーム日本語テキストを描き直すのは、非力なPi Zero 2Wには重すぎるため。"""
        w = self.canvas.get_width()
        h = self.canvas.get_height()
        self.canvas.fill((24, 24, 30))

        if not MANUAL_PAGES:
            return

        now = time.time()
        if now - self.manual_page_start_time >= MANUAL_PAGE_SECONDS:
            self.manual_page_start_time = now
            self.manual_page_index = (self.manual_page_index + 1) % len(MANUAL_PAGES)

        page_index = self.manual_page_index % len(MANUAL_PAGES)
        max_width = w - 64
        cache_key = (page_index, w, h)

        if self._manual_render_cache.get("key") != cache_key:
            page = MANUAL_PAGES[page_index]

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
                f"{page_index + 1} / {len(MANUAL_PAGES)}　（ボタンで閉じる）",
                max_width, start_size=16, min_size=11, color=(150, 150, 150))

            self._manual_render_cache = {
                "key": cache_key,
                "title_surf": title_surf,
                "body_surfaces": body_surfaces,
                "footer_surf": footer_surf,
            }

        title_surf = self._manual_render_cache["title_surf"]
        body_surfaces = self._manual_render_cache["body_surfaces"]
        footer_surf = self._manual_render_cache["footer_surf"]

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
        """ボタン長押しで呼ばれる。接続情報（QR・SSID・パスワード）の画面を表示する。
        既にスタンドアロンモードでアクセスポイントが常時起動済みの場合は、
        新たに立て直さず、その情報をそのまま表示するだけにする。"""
        if self.wifi_setup_active:
            return

        self.manual_active = False
        self._hide_qr()

        if self.standalone_active:
            # 既にAPが常時起動しているので、情報表示だけ行う
            self.wifi_setup_active = True
            self.wifi_setup_start_time = time.time()
            log("スタンドアロンモードの接続情報を表示します")
            return

        settings = load_settings(IMAGE_FOLDER, {
            "setup_ap_ssid": wifi_setup.default_setup_ssid(WIFI_SETUP_SSID_PREFIX),
            "setup_ap_password": WIFI_SETUP_DEFAULT_PASSWORD,
        })
        ssid = settings.get("setup_ap_ssid") or wifi_setup.default_setup_ssid(WIFI_SETUP_SSID_PREFIX)
        password = settings.get("setup_ap_password", WIFI_SETUP_DEFAULT_PASSWORD)

        log(f"Wi-Fiセットアップモードへ切り替え中... SSID={ssid}")

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
        """接続情報の画面を閉じる。スタンドアロンモード中はアクセスポイント自体は
        維持したまま画面だけ通常表示に戻す。それ以外（一時的な新規設定中）は
        アクセスポイントごと終了する。"""
        if not self.wifi_setup_active:
            return
        self.wifi_setup_active = False

        if self.standalone_active:
            log("接続情報表示を閉じました（スタンドアロンのアクセスポイントは維持されます）")
            return

        log("Wi-Fiセットアップモードを終了しています...")
        wifi_setup.stop_hotspot()
        log("通常モードに戻りました")

    def _enter_standalone_mode(self):
        """外部Wi-Fiが見つからない時に自動的に呼ばれる。自分専用のアクセスポイントを
        画面には出さず裏側で常時起動しておき、ボタンが押された時だけ接続情報を表示する。"""
        if self.standalone_active or self.wifi_setup_active:
            return

        settings = load_settings(IMAGE_FOLDER, {
            "setup_ap_ssid": wifi_setup.default_setup_ssid(WIFI_SETUP_SSID_PREFIX),
            "setup_ap_password": WIFI_SETUP_DEFAULT_PASSWORD,
        })
        ssid = settings.get("setup_ap_ssid") or wifi_setup.default_setup_ssid(WIFI_SETUP_SSID_PREFIX)
        password = settings.get("setup_ap_password", WIFI_SETUP_DEFAULT_PASSWORD)

        log(f"既知のWi-Fiが見つからないため、スタンドアロンモードへ移行します。SSID={ssid}")
        ok, out, err = wifi_setup.start_hotspot(ssid, password)
        if not ok:
            log(f"スタンドアロン用アクセスポイントの起動に失敗しました: {err or out}")
            return

        self.standalone_active = True
        self.wifi_setup_ssid = ssid
        self.wifi_setup_password = password
        self._build_wifi_setup_surfaces()
        log("スタンドアロンモードで待機中（画面は通常表示のまま、ボタン長押しで接続情報を表示できます）")

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
        if self.standalone_active:
            title_text = "スタンドアロンモード（外部Wi-Fi無し）"
            close_text = "（ボタンで閉じる。アクセスポイントは維持されます）"
        else:
            title_text = "Wi-Fiセットアップモード"
            close_text = "（ボタンでキャンセル）"
        lines = [
            (title_text, 26, (255, 255, 255)),
            (f"SSID: {self.wifi_setup_ssid}", 22, (230, 230, 230)),
            (f"パスワード: {self.wifi_setup_password}", 22, (230, 230, 230)),
            (setup_url, 18, (150, 220, 150)),
            (close_text, 18, (200, 200, 200)),
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
        # 表示中+数枚先までの通常画像を、1フレームにつき最大1枚だけ先読みする
        # （固定表示画像は常に別枠で常駐しているため対象外）
        self._ensure_window_cached()

        with self._lock:
            ordered = self.ordered_files
            cur_idx = self.current_pop_index
            next_idx = self.next_pop_index

        if not ordered:
            # 写真が1枚も無い（＝購入直後の無垢な状態）場合は、
            # 「画像がありません」ではなく取扱説明を自動的にループ表示する
            self.draw_manual_screen()
            return

        n = len(ordered)
        current_img = self._get_surface(ordered[cur_idx % n])
        if current_img is None:
            # デコード失敗によりその場でordered_filesから除外された等、
            # 今回のフレームだけ取得できなかった場合は何も描かずスキップする
            # （次フレームでは更新後のordered_filesを基準に再評価される）
            return

        if self.qr_active:
            # QR表示中はスライドショーを一時停止する。時間の巻き戻しは
            # show_qr_code/_hide_qr側で行うので、ここでは現在の画像を
            # そのまま静止表示するだけでよい（切り替え判定は一切行わない）。
            self.canvas.blit(current_img, (0, 0))
            return

        now = time.time()
        elapsed = now - self.pop_start_time

        if elapsed >= IMAGE_INTERVAL and not self.in_transition and n > 1:
            self.in_transition = True
            with self._lock:
                self.next_pop_index = (cur_idx + 1) % n
                next_idx = self.next_pop_index
            self.transition_start_time = now

        if self.in_transition:
            next_img = self._get_surface(ordered[next_idx % n])
            if next_img is None:
                # 次画像の取得に失敗。この回の遷移は諦めて現在の画像のまま
                # 据え置き、次フレームで態勢を立て直す
                self.in_transition = False
                self.canvas.blit(current_img, (0, 0))
                return

            # 実フレームレートに関わらず、指定した秒数ちょうどで切り替わるよう
            # 実経過時間を基準にprogressを計算する（フレーム数基準だと、
            # 非力な機器で実際のFPSが落ちた時に想定より大幅に長くなってしまうため）
            trans_elapsed = now - self.transition_start_time
            progress = min(trans_elapsed / TRANSITION_DURATION, 1.0)

            self._draw_transition_frame(current_img, next_img, progress)

            if progress >= 1.0:
                with self._lock:
                    self.current_pop_index = next_idx
                self.pop_start_time = now
                self.in_transition = False
        else:
            self.canvas.blit(current_img, (0, 0))

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
                        if event.key == pygame.K_r:
                            # Mac等、GPIOボタンが無い環境での表示リセット確認用
                            self._reset_display_to_pinned_only()
                        if event.key == pygame.K_s:
                            # Mac等、GPIOボタンが無い環境でのシャットダウン確認用
                            self._trigger_button_shutdown()
                        if event.key == pygame.K_e:
                            # Mac等、GPIOボタンが無い環境でのUSB書き出し待受け確認用
                            if self.export_standby_active:
                                self._cancel_export_standby(reason="キー操作によりキャンセル")
                            else:
                                self._enter_export_standby()

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

                    current_notice_mtime = notice_mtime(IMAGE_FOLDER)
                    if current_notice_mtime != self.last_notice_mtime:
                        self.last_notice_mtime = current_notice_mtime
                        notice = load_notice(IMAGE_FOLDER)
                        if notice and notice.get("message"):
                            self._show_notice(notice["message"], sticky=notice.get("sticky", False),
                                               fullscreen=notice.get("fullscreen", False))
                        else:
                            # 通知ファイルが削除された（USBメモリー取り外し検知等）
                            # ＝表示中のsticky通知（全画面のものも含む）を即座に消す合図
                            self._hide_notice()

                    # Web側で保存済みWi-Fi情報を削除するなど、接続状態が変わりうる
                    # 操作があった直後の合図。standalone_active等のフラグは最大
                    # STANDALONE_CHECK_INTERVAL秒古い可能性があるため、この合図を
                    # 検知したら定期チェックを待たず、このフレーム内で即座に
                    # 再判定させる（下のスタンドアロン自動移行チェックへ委ねる）
                    current_network_recheck_mtime = network_recheck_mtime(IMAGE_FOLDER)
                    if current_network_recheck_mtime != self.last_network_recheck_mtime:
                        self.last_network_recheck_mtime = current_network_recheck_mtime
                        if not self.wifi_setup_active and not self.standalone_active:
                            log("ネットワーク状態の即時再判定要求を検知しました")
                            self.last_standalone_check_time = now - STANDALONE_CHECK_INTERVAL

                    # USBオフラインアップデート：USB挿入時にpophug-usb-importが
                    # 検出・ステージングした（.usb_update_pending.json）、または
                    # ボタン操作で確定・見送りされて消えた、のどちらかの変化を検知する。
                    # 適用処理中(_usb_update_applying)は、その処理自身がファイルを
                    # 削除するタイミングと重なるため、ここでの読み直しは行わない
                    # （既にメモリ上のself.usb_update_pendingで処理を続けているため不要）。
                    current_usb_update_pending_mtime = usb_update_pending_mtime(IMAGE_FOLDER)
                    if (current_usb_update_pending_mtime != self.last_usb_update_pending_mtime
                            and not self._usb_update_applying):
                        self.last_usb_update_pending_mtime = current_usb_update_pending_mtime
                        self.usb_update_pending = load_usb_update_pending(IMAGE_FOLDER)
                        if self.usb_update_pending:
                            log(f"USBアップデートパッケージを検出しました: "
                                f"v{self.usb_update_pending.get('version')}"
                                f"（現在v{self.usb_update_pending.get('current_version')}）。"
                                f"本体ボタンを{USB_UPDATE_CONFIRM_HOLD_SECONDS}秒以上"
                                f"長押しすると適用します")

                    # USB書き出し（吸い出し）機能：待受け中に、別プロセス
                    # (pophug-usb-import)が書き出しを完了・見送りして状態ファイルを
                    # 消した場合の検知。ここで検知した時点で既に通知(sticky notice)は
                    # 別途表示されているはずなので、ここではUIの専有画面だけを解除する。
                    current_export_standby_mtime = export_standby_mtime(IMAGE_FOLDER)
                    if current_export_standby_mtime != self.last_export_standby_mtime:
                        self.last_export_standby_mtime = current_export_standby_mtime
                        if self.export_standby_active and load_export_standby(IMAGE_FOLDER) is None:
                            self.export_standby_active = False
                            log("USB書き出し待受け状態の変化を検知し、待受け画面を終了しました")

                if now - self.last_scan_time >= RESCAN_INTERVAL:
                    self.last_scan_time = now
                    self.load_pop_images()

                if self.wifi_setup_active or self.standalone_active:
                    # 2秒おきに、外部(Web側の接続操作)によってアクセスポイントが
                    # 既に落とされていないか・タイムアウトしていないかを確認する
                    if now - self.last_wifi_setup_check_time >= 2:
                        self.last_wifi_setup_check_time = now
                        # retry-known実行中は既知Wi-Fiを試すために一時的にAPを落としているだけなので、
                        # その間だけは「外部要因でAPが終了した」との誤検知を避ける
                        if not wifi_setup.is_hotspot_active() and not wifi_setup.is_wifi_retry_in_progress():
                            # 外部要因(Web側での接続成功など)でアクセスポイントが終了した
                            if self.standalone_active:
                                log("アクセスポイントが終了したため、スタンドアロンモードを終了します")
                            elif self.wifi_setup_active:
                                log("Wi-Fi接続が完了したようです。通常モードに戻ります")
                            self.standalone_active = False
                            self.wifi_setup_active = False
                        elif self.wifi_setup_active and now - self.wifi_setup_start_time >= WIFI_SETUP_TIMEOUT_SECONDS:
                            log("接続情報の表示がタイムアウトしました")
                            self.exit_wifi_setup_mode()

                if (self.export_standby_active
                        and now - self.export_standby_start_time >= EXPORT_STANDBY_TIMEOUT_SECONDS):
                    self._cancel_export_standby(reason="タイムアウト")

                # 知っているWi-Fiが見つからない場合、自動的にスタンドアロンモードへ移行する。
                # 起動直後は少し待ってから最初の判定を行い、以後は定期的に再判定する
                # （途中でWi-Fi接続が切れた場合の検知も兼ねる）。
                if (STANDALONE_AUTO_ENABLED and not self.wifi_setup_active
                        and not self.standalone_active
                        and now - self.last_standalone_check_time >= STANDALONE_CHECK_INTERVAL):
                    self.last_standalone_check_time = now
                    if not wifi_setup.is_client_connected():
                        self._enter_standalone_mode()

                if self._shutting_down:
                    # シャットダウンが確定した後は、実際に電源が落ちるまで
                    # 他の何よりも優先してこの専有画面を出し続ける
                    self.draw_shutdown_screen()
                elif self._usb_update_applying:
                    # USBアップデートの適用処理が進行中（ボタン長押しで確定済み、
                    # まだサービス再起動前）。他の何より優先して専有画面を出し続け、
                    # スライドショーや古い通知・バッジが再表示されて
                    # 「失敗したのでは」と誤解されることのないようにする
                    self.draw_usb_update_applying_screen()
                elif self.wifi_setup_active:
                    self.draw_wifi_setup_screen()
                elif self.manual_active:
                    self.draw_manual_screen()
                    self.draw_button_hold_overlay()
                elif self.export_standby_active:
                    self.draw_export_standby_screen()
                elif self._fullscreen_notice_active:
                    # USBメモリーに読み込める内容が何も無かった場合の専有画面。
                    # ボタン操作（QR表示等）よりは優先度を落とし、Wi-Fiセットアップ・
                    # 取扱説明のような能動的な操作モードよりは低い位置に置いている
                    # （それらは明示的にユーザーが呼び出した操作のため優先させる）。
                    # USBメモリーが抜かれるまで表示され続ける
                    self.draw_fullscreen_notice()
                else:
                    self.draw_pop_mode()
                    if self.qr_active:
                        self.draw_qr_overlay()
                    self.draw_usb_update_badge()
                    self.draw_notice_overlay()
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

                # ウォッチドッグへの生存通知。ここまで到達した＝イベント処理・描画・
                # 画面更新が一通り正常に完了した合図なので、フレームごとではなく
                # 間隔を空けて送る（毎フレーム送っても意味は増えず、無駄が増えるだけのため）。
                # 実際にメインループが固まった場合はこの行自体に到達しなくなるので、
                # 通知が途絶え、systemd側のWatchdogSec=経過後に自動再起動される。
                if now - self._last_watchdog_time >= self._watchdog_interval:
                    self._last_watchdog_time = now
                    sd_watchdog.notify_alive()
        except KeyboardInterrupt:
            log("Ctrl+Cを検知、終了します")
            sd_watchdog.notify_stopping()
            pygame.quit()
            sys.exit(0)


if __name__ == "__main__":
    if "--version" in sys.argv or "-v" in sys.argv:
        print(f"KitchenCar POP Signage v{__version__}")
        sys.exit(0)
    app = PopSignage()
    app.run()
