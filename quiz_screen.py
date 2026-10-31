# ============================================
# KitchenCar POP Signage - quiz_screen.py
#
# 2択クイズモードの画面描画（pygame）。状態遷移・出題・集計は quiz_engine.py が持ち、
# ここは「今の状態を画面に描く」ことだけを担当する（main.pyを肥大化させないため別ファイル）。
#
# 画面は3種類：出題 / 結果・解説 / ラウンド終了の成績発表。
# キャンバスの縦横比（横長・縦長）に応じてレイアウトを切り替える。
# 日本語テキストの描画はPi Zero 2Wには重いため、静的な部分は1枚のSurfaceに
# まとめてキャッシュし、内容が変わった時だけ描き直す。
# ============================================

import os
import pygame

from quiz_engine import STATE_QUESTION, STATE_RESULT, STATE_ROUND_END

BG = (18, 18, 26)
PANEL_BG = (34, 34, 48)
TEXT = (245, 245, 250)
SUBTEXT = (170, 170, 190)
COLOR_A = (46, 110, 190)
COLOR_B = (214, 84, 120)
OK_COLOR = (46, 160, 90)
NG_COLOR = (200, 70, 70)
GAUGE_COLOR = (255, 200, 60)
CHOICE_COLORS = (COLOR_A, COLOR_B)
CHOICE_LETTERS = ("A", "B")


class QuizScreen:
    def __init__(self, get_font, data_folder):
        """get_font(size) -> pygame.font.Font（main.pyのget_japanese_fontを渡す）。
        data_folder: クイズのデータフォルダ（画像は <data_folder>/images/ に置く）。"""
        self.get_font = get_font
        self.image_folder = os.path.join(data_folder, "images")
        self._img_cache = {}
        self._img_cache_qid = None
        self._layer_key = None
        self._layer = None
        self._footer_key = None
        self._footer = None

    # ---------------- 部品 ----------------

    @staticmethod
    def _quantize(size):
        """フォントサイズを段階化する（サイズごとにFontオブジェクトが作られて
        メモリを食うのを避けるため、使うサイズの種類を絞る）。"""
        steps = [12, 14, 16, 20, 24, 28, 34, 40, 48, 58, 70, 84, 100, 120, 144, 170, 200, 240]
        best = steps[0]
        for s in steps:
            if s <= size:
                best = s
        return best

    def _wrap(self, text, font, max_w):
        lines = []
        for para in str(text).split("\n"):
            cur = ""
            for ch in para:
                if cur and font.size(cur + ch)[0] > max_w:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            lines.append(cur)
        return lines

    def _text_block(self, text, w, h, max_size, color=TEXT, min_size=14, align="center"):
        """w×hの枠に収まる最大のフォントサイズで折り返し描画したSurface（透過）を返す。
        min_sizeでも収まらない場合は、収まる行数で切って末尾に「…」を付ける。"""
        size = self._quantize(max_size)
        while True:
            font = self.get_font(size)
            lines = self._wrap(text, font, w)
            lh = int(size * 1.25)
            if len(lines) * lh <= h or size <= min_size:
                break
            size = self._quantize(int(size * 0.85))
        max_lines = max(1, h // lh)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            while lines[-1] and font.size(lines[-1] + "…")[0] > w:
                lines[-1] = lines[-1][:-1]
            lines[-1] += "…"
        surf = pygame.Surface((w, max(1, len(lines) * lh)), pygame.SRCALPHA)
        for i, line in enumerate(lines):
            ts = font.render(line, True, color)
            x = (w - ts.get_width()) // 2 if align == "center" else 0
            surf.blit(ts, (x, i * lh))
        return surf

    def _img(self, name, w, h):
        """画像を枠(w×h)に収まる大きさに縮尺したSurface。読めなければNone。
        現在の問題の分だけキャッシュし、問題が変わると破棄する（メモリ節約）。"""
        key = (name, w, h)
        if key in self._img_cache:
            return self._img_cache[key]
        surf = None
        try:
            path = os.path.join(self.image_folder, os.path.basename(name))
            img = pygame.image.load(path)
            try:
                img = img.convert_alpha()
            except pygame.error:
                pass
            iw, ih = img.get_size()
            if iw > 0 and ih > 0 and w > 0 and h > 0:
                scale = min(w / iw, h / ih)
                surf = pygame.transform.smoothscale(
                    img, (max(1, int(iw * scale)), max(1, int(ih * scale))))
        except Exception:
            surf = None
        self._img_cache[key] = surf
        return surf

    def _draw_part(self, surf, rect, part, max_size, color=TEXT, vertical_split=True):
        """{"text","image"}の中身をrect内に描く。画像のみ/テキストのみ/併用に対応。"""
        x, y, w, h = rect
        if w <= 0 or h <= 0:
            return
        text = part.get("text", "")
        image = part.get("image", "")
        img_rect = txt_rect = None
        if image and text:
            if vertical_split:
                ih = int(h * 0.62)
                img_rect, txt_rect = (x, y, w, ih), (x, y + ih + 4, w, h - ih - 4)
            else:
                iw = int(w * 0.5)
                img_rect, txt_rect = (x, y, iw, h), (x + iw + 8, y, w - iw - 8, h)
        elif image:
            img_rect = rect
        else:
            txt_rect = rect
        if img_rect:
            im = self._img(image, img_rect[2], img_rect[3])
            if im is not None:
                surf.blit(im, (img_rect[0] + (img_rect[2] - im.get_width()) // 2,
                               img_rect[1] + (img_rect[3] - im.get_height()) // 2))
            elif not text:
                txt_rect, text = img_rect, "（画像を読み込めません）"
        if txt_rect and text:
            tb = self._text_block(text, txt_rect[2], txt_rect[3], max_size, color)
            surf.blit(tb, (txt_rect[0], txt_rect[1] + max(0, (txt_rect[3] - tb.get_height()) // 2)))

    def _badge(self, surf, cx, cy, d, letter, color, text_color=(255, 255, 255)):
        pygame.draw.circle(surf, color, (cx, cy), d // 2)
        font = self.get_font(self._quantize(int(d * 0.7)))
        ts = font.render(letter, True, text_color)
        surf.blit(ts, (cx - ts.get_width() // 2, cy - ts.get_height() // 2))

    # ---------------- レイアウト ----------------

    @staticmethod
    def _frame(w, h):
        m = max(10, min(w, h) // 36)
        header_h = max(30, int(h * 0.10))
        footer_h = max(24, int(h * 0.075))
        body = (m, header_h, w - 2 * m, h - header_h - footer_h - m)
        return m, header_h, footer_h, body

    def _draw_header(self, surf, w, m, header_h, session):
        number, size, correct = session.round_progress()
        font = self.get_font(self._quantize(int(header_h * 0.62)))
        left = f"第{number}問 / {size}" if size else "クイズ"
        surf.blit(font.render(left, True, TEXT), (m, (header_h - font.get_height()) // 2))
        if size:
            right = f"正解 {correct}問"
            ts = font.render(right, True, SUBTEXT)
            surf.blit(ts, (w - m - ts.get_width(), (header_h - ts.get_height()) // 2))

    # ---------------- 画面ごとの描画 ----------------

    def _draw_question(self, surf, w, h, session):
        m, header_h, footer_h, (bx, by, bw, bh) = self._frame(w, h)
        self._draw_header(surf, w, m, header_h, session)
        q = session.current
        landscape = w >= h
        gap = m
        # 選択肢A・Bの枠は、結果画面のA/B行と同じくらいの大きさに抑え、
        # 残りの領域を問題文（画像）に使う（枠が大きすぎると問題が窮屈になるため）
        if landscape:
            ph = int(bh * 0.31)
            qh = bh - ph - gap
        else:
            ph = int(bh * 0.17)
            qh = bh - 2 * ph - 2 * gap
        pygame.draw.rect(surf, PANEL_BG, (bx, by, bw, qh), border_radius=m)
        pad = m
        self._draw_part(surf, (bx + pad, by + pad, bw - 2 * pad, qh - 2 * pad), q["question"],
                        max_size=int(h * 0.12), vertical_split=landscape)
        cy = by + qh + gap
        if landscape:
            pw = (bw - gap) // 2
            rects = [(bx, cy, pw, ph), (bx + pw + gap, cy, pw, ph)]
        else:
            rects = [(bx, cy, bw, ph), (bx, cy + ph + gap, bw, ph)]
        for i, (rx, ry, rw, rh) in enumerate(rects):
            pygame.draw.rect(surf, CHOICE_COLORS[i], (rx, ry, rw, rh), border_radius=m)
            d = max(36, min(int(rh * 0.5), int(rw * 0.2)))
            self._badge(surf, rx + pad + d // 2, ry + rh // 2, d, CHOICE_LETTERS[i], (255, 255, 255), CHOICE_COLORS[i])
            cx0 = rx + pad * 2 + d
            self._draw_part(surf, (cx0, ry + pad, rx + rw - pad - cx0, rh - 2 * pad), q["choices"][i],
                            max_size=min(int(h * 0.09), int(rh * 0.42)), color=(255, 255, 255),
                            vertical_split=False)

    def _draw_result(self, surf, w, h, session):
        m, header_h, footer_h, (bx, by, bw, bh) = self._frame(w, h)
        self._draw_header(surf, w, m, header_h, session)
        q = session.current
        correct = session.is_correct()
        landscape = w >= h
        if landscape:
            lw = int(bw * 0.52)
            left = (bx, by, lw, bh)
            right = (bx + lw + m, by, bw - lw - m, bh)
        else:
            lh = int(bh * 0.55)
            left = (bx, by, bw, lh)
            right = (bx, by + lh + m, bw, bh - lh - m)

        # 左（上）: 判定 + A/Bの回答割合
        lx, ly, lw, lh = left
        vh = int(lh * 0.24)
        pygame.draw.rect(surf, OK_COLOR if correct else NG_COLOR, (lx, ly, lw, vh), border_radius=m)
        verdict = "◯ 正解！" if correct else "× ざんねん！"
        vb = self._text_block(verdict, lw - 2 * m, vh - m, int(vh * 0.8), (255, 255, 255), min_size=20)
        surf.blit(vb, (lx + m, ly + (vh - vb.get_height()) // 2))

        a, b = session.store.get_counts(q["id"])
        pa, pb = session.result_percentages()
        total = a + b

        # 見出し：このバーが「みんなの回答割合」であることを明示する
        cap_h = max(18, int(h * 0.045))
        cf = self.get_font(self._quantize(max(14, int(cap_h * 0.8))))
        cap = "みんなの回答割合" + (f"（{total}人）" if total else "（まだデータなし）")
        ct = cf.render(cap, True, SUBTEXT)
        rows_y = ly + vh + m
        surf.blit(ct, (lx + m // 2, rows_y + (cap_h - ct.get_height()) // 2))
        rows_y += cap_h
        rows_h = lh - vh - m - cap_h
        row_h = (rows_h - m) // 2
        pad = max(4, m // 2)
        gap = max(3, pad // 2)
        for i in range(2):
            ry = rows_y + i * (row_h + m)
            is_answer = (q["answer"] == i)
            is_mine = (session.selected == i)
            pygame.draw.rect(surf, PANEL_BG, (lx, ry, lw, row_h), border_radius=m)
            if is_answer:
                pygame.draw.rect(surf, OK_COLOR, (lx, ry, lw, row_h), width=max(3, m // 3), border_radius=m)
            # 下段=割合バー（バーは行の下端に固定。文字とは重ならない）
            bar_h = max(10, int(row_h * 0.2))
            bar_y = ry + row_h - pad - bar_h
            bar_x = lx + m
            bar_w = lw - 2 * m
            # 上段=バッジ・選択肢・タグ・割合（バーより上に収める）
            l1 = max(20, bar_y - gap - (ry + pad))
            d = min(l1, int(row_h * 0.5))
            self._badge(surf, lx + m + d // 2, ry + pad + l1 // 2, d, CHOICE_LETTERS[i], CHOICE_COLORS[i])
            cx0 = lx + m + d + m
            pct = pa if i == 0 else pb
            pf = self.get_font(self._quantize(max(16, int(l1 * 0.8))))
            pt = pf.render(f"{pct}%", True, TEXT)
            px = lx + lw - m - pt.get_width()
            surf.blit(pt, (px, ry + pad + (l1 - pt.get_height()) // 2))
            # 「あなた」「正解」のタグは割合の左に小さなラベルで並べる
            tags = []
            if is_mine:
                tags.append(("あなた", (255, 255, 255)))
            if is_answer:
                tags.append(("正解", OK_COLOR))
            tgf = self.get_font(self._quantize(max(12, int(l1 * 0.38))))
            # 狭くて選択肢の文字が潰れそうなら「正解」タグは省く（緑の枠で正解は分かる）
            need = sum(tgf.size(t)[0] + m + m // 2 for t, _ in tags)
            if len(tags) > 1 and (px - m // 2 - need) - cx0 < (lx + lw - cx0) * 0.3:
                tags = [t for t in tags if t[0] != "正解"]
            tag_x = px - m // 2
            for label, col in tags:
                ts = tgf.render(label, True, (14, 18, 30))
                tw, th = ts.get_width() + m, ts.get_height() + 4
                tx = tag_x - tw
                ty = ry + pad + (l1 - th) // 2
                pygame.draw.rect(surf, col, (tx, ty, tw, th), border_radius=th // 2)
                surf.blit(ts, (tx + m // 2, ty + 2))
                tag_x = tx - m // 2
            self._draw_part(surf, (cx0, ry + pad, tag_x - cx0, l1),
                            q["choices"][i], max_size=int(l1 * 0.6), vertical_split=False)
            pygame.draw.rect(surf, (60, 60, 80), (bar_x, bar_y, bar_w, bar_h), border_radius=6)
            if pct:
                pygame.draw.rect(surf, CHOICE_COLORS[i],
                                 (bar_x, bar_y, max(6, int(bar_w * pct / 100)), bar_h), border_radius=6)

        # 右（下）: 解説
        rx, ry, rw, rh = right
        pygame.draw.rect(surf, PANEL_BG, (rx, ry, rw, rh), border_radius=m)
        ex = q.get("explanation", {})
        if ex.get("text") or ex.get("image"):
            tf = self.get_font(self._quantize(max(16, int(h * 0.05))))
            surf.blit(tf.render("解説", True, SUBTEXT), (rx + m, ry + m // 2))
            top = tf.get_height() + m
            self._draw_part(surf, (rx + m, ry + top, rw - 2 * m, rh - top - m), ex,
                            max_size=int(h * 0.07), vertical_split=landscape)
        else:
            nb = self._text_block("次の問題へ進みます", rw - 2 * m, rh - 2 * m, int(h * 0.06), SUBTEXT)
            surf.blit(nb, (rx + m, ry + (rh - nb.get_height()) // 2))

    def _draw_round_end(self, surf, w, h, session):
        m, header_h, footer_h, (bx, by, bw, bh) = self._frame(w, h)
        number, size, correct = session.round_progress()
        pygame.draw.rect(surf, PANEL_BG, (bx, by, bw, bh), border_radius=m)
        total = size
        ratio = (correct / total) if total else 0
        if total and correct == total:
            comment = "全問正解！パーフェクト！"
        elif ratio >= 0.6:
            comment = "よくできました！"
        else:
            comment = "また挑戦してね！"
        t1 = self._text_block("おつかれさま！", bw - 2 * m, int(bh * 0.2), int(h * 0.11), SUBTEXT)
        t2 = self._text_block(f"{total}問中 {correct}問 正解", bw - 2 * m, int(bh * 0.3), int(h * 0.18), TEXT)
        t3 = self._text_block(comment, bw - 2 * m, int(bh * 0.2), int(h * 0.10),
                              OK_COLOR if ratio >= 0.6 else GAUGE_COLOR)
        gap = int(bh * 0.04)
        total_h = t1.get_height() + t2.get_height() + t3.get_height() + 2 * gap
        y = by + (bh - total_h) // 2
        for t in (t1, t2, t3):
            surf.blit(t, (bx + (bw - t.get_width()) // 2, y))
            y += t.get_height() + gap

    def _draw_empty(self, surf, w, h):
        tb = self._text_block("クイズの問題が登録されていません\nWeb設定画面の「クイズ」から登録してください",
                              w - 80, h // 2, int(h * 0.07), SUBTEXT)
        surf.blit(tb, ((w - tb.get_width()) // 2, (h - tb.get_height()) // 2))

    # ---------------- 公開API ----------------

    def _footer_text(self, session, now):
        hold = int(session.settings["quiz_hold_reset_seconds"])
        if session.state == STATE_QUESTION:
            return f"A・Bのボタンで答えてね　／　どちらかを{hold}秒長押しで最初から"
        if session.state == STATE_RESULT:
            remain = max(0, int(session.settings["quiz_result_idle_seconds"] - (now - session.last_event_time)) + 1)
            return f"ボタンを押すと次へ　（あと{remain}秒で自動で次へ）"
        remain = max(0, int(session.settings["quiz_round_end_seconds"] - (now - session.last_event_time)) + 1)
        return f"ボタンを押すと次の人へ　（あと{remain}秒で自動で次へ）"

    def draw(self, canvas, session, now, gauge=None):
        """クイズ画面を描く。gaugeは長押しリセットの進捗（None または 0.0〜1.0）。"""
        w, h = canvas.get_size()
        if session.current is None:
            canvas.fill(BG)
            key = ("empty", w, h)
            if self._layer_key != key:
                layer = pygame.Surface((w, h)); layer.fill(BG)
                self._draw_empty(layer, w, h)
                self._layer, self._layer_key = layer, key
            canvas.blit(self._layer, (0, 0))
            return

        q = session.current
        if q["id"] != self._img_cache_qid:
            self._img_cache = {}
            self._img_cache_qid = q["id"]
        counts = session.store.get_counts(q["id"])
        key = (session.state, q["id"], session.selected, w, h, counts, session.round_progress())
        if key != self._layer_key:
            layer = pygame.Surface((w, h))
            layer.fill(BG)
            if session.state == STATE_QUESTION:
                self._draw_question(layer, w, h, session)
            elif session.state == STATE_RESULT:
                self._draw_result(layer, w, h, session)
            else:
                self._draw_round_end(layer, w, h, session)
            self._layer, self._layer_key = layer, key
        canvas.blit(self._layer, (0, 0))

        # フッター（毎秒変わる案内文。小さいので別キャッシュ）
        m, header_h, footer_h, _ = self._frame(w, h)
        ftext = self._footer_text(session, now)
        fkey = (ftext, w, h)
        if fkey != self._footer_key:
            fs = self.get_font(self._quantize(max(14, int(footer_h * 0.55))))
            ts = fs.render(ftext, True, SUBTEXT)
            if ts.get_width() > w - 2 * m:
                ts = pygame.transform.smoothscale(ts, (w - 2 * m, max(1, int(ts.get_height() * (w - 2 * m) / ts.get_width()))))
            self._footer, self._footer_key = ts, fkey
        fy = h - footer_h
        if gauge is None:   # 長押しゲージを出している間は、下のヒント文は隠す
            canvas.blit(self._footer, ((w - self._footer.get_width()) // 2, fy + (footer_h - self._footer.get_height()) // 2))

        if gauge is not None:
            band_h = max(60, int(h * 0.16))
            overlay = pygame.Surface((w, band_h), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 215))
            canvas.blit(overlay, (0, h - band_h))
            lf = self.get_font(self._quantize(max(16, int(band_h * 0.3))))
            lt = lf.render("そのまま押し続けると、最初からやり直します", True, (255, 255, 255))
            canvas.blit(lt, ((w - lt.get_width()) // 2, h - band_h + int(band_h * 0.15)))
            bw = int(w * 0.7); bh2 = max(10, int(band_h * 0.18))
            bx = (w - bw) // 2; by2 = h - int(band_h * 0.38)
            pygame.draw.rect(canvas, (70, 70, 90), (bx, by2, bw, bh2), border_radius=bh2 // 2)
            pygame.draw.rect(canvas, GAUGE_COLOR, (bx, by2, max(1, int(bw * max(0.0, min(1.0, gauge)))), bh2),
                             border_radius=bh2 // 2)
