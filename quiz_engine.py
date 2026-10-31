# ============================================
# KitchenCar POP Signage - quiz_engine.py
#
# 2択クイズモードの中核ロジック（画面・pygame・Flaskに依存しない）。
# 問題データの保存/検証、出題順（登録順/ランダム）、出題→結果→次の問題の
# 状態遷移、回答集計を担当する。時刻はすべて引数nowで受け取るため、
# headless環境でも時間経過を含めて検証できる。
#
# データは images/ とは別の quiz_data/ に保存する（購入者ごとのデータなので
# アップデートで上書き・削除しない。pophug-update-apply の保護対象に追加すること）。
# ============================================

import io
import os
import random
import threading
import time

import json

from signage_state import _atomic_write_json


def load_json_file(path, default):
    """JSONを読む。無い・壊れている場合はdefaultを返す（壊れても起動を止めない）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

QUESTIONS_FILENAME = "questions.json"
STATS_FILENAME = "stats.json"

ORDER_SEQUENTIAL = "sequential"
ORDER_RANDOM = "random"

STATE_QUESTION = "question"
STATE_RESULT = "result"
STATE_ROUND_END = "round_end"   # 1ラウンド終了後の成績発表画面

# 設定の既定値と範囲（検証はvalidate_quiz_settings()に一元化する）
QUIZ_DEFAULTS = {
    "quiz_order": ORDER_SEQUENTIAL,       # sequential / random
    "quiz_result_idle_seconds": 30,       # 結果・解説表示中、無操作でこの秒数経つと次の問題へ
    "quiz_question_idle_seconds": 60,     # 出題中、無回答でこの秒数経つと次の問題へ（0=無効）
    "quiz_round_size": 5,                 # 1人分（1ラウンド）の問題数（0=ラウンド無し・成績を数えない）
    "quiz_round_end_seconds": 20,         # 成績発表の表示中、無操作でこの秒数経つと次のラウンドへ
    "quiz_hold_reset_seconds": 3,         # 回答ボタン長押しで「最初から」になるまでの秒数（画面側で使用）
    "quiz_color_a": "blue",               # 実機のAボタンの色（画面のA表示をボタンの色に合わせる）
    "quiz_color_b": "red",                # 実機のBボタンの色
}
# 選べるボタンの色（実機のボタンが5色ある想定）。画面側(quiz_screen.py)が色を割り当てる
BUTTON_COLORS = ("blue", "yellow", "green", "white", "red")
# A・Bボタンのキーコード割り当て（0=未割り当て→config.pyの既定値）と、画面表示用のキー名
QUIZ_KEY_SETTINGS = ("quiz_key_a", "quiz_key_b")
QUIZ_KEY_NAME_SETTINGS = ("quiz_key_a_name", "quiz_key_b_name")
QUIZ_SETTING_KEYS = tuple(QUIZ_DEFAULTS) + QUIZ_KEY_SETTINGS + QUIZ_KEY_NAME_SETTINGS
MAX_QUESTIONS = 100

# 回答・成績発表の直後に、ボタンのチャタリング（1回の押下が2回に見える）で
# 意図せず先へ進むのを防ぐだけの内部定数。人が押し直す間隔より十分短く、設定は設けない。
ADVANCE_DEBOUNCE_SECONDS = 0.3

_RANGES = {
    "quiz_result_idle_seconds": (5, 600),
    "quiz_question_idle_seconds": (0, 600),
    "quiz_round_size": (0, 50),
    "quiz_round_end_seconds": (5, 600),
    "quiz_hold_reset_seconds": (1, 10),
}

STATS_FLUSH_EVERY = 5  # 回答を何件ためてからSDカードへ書くか（書き込み回数の抑制）
MAX_TEXT_LEN = 200


def validate_quiz_settings(updates):
    """設定の更新辞書を検証し、(cleaned, errors)を返す。errorsは[(キー名, メッセージ), ...]。
    signage_state.validate_settings_updates()から呼ばれ、Web設定画面・USB設定ファイルの
    両方で同じ基準が使われる（検証ルールの一元化）。"""
    cleaned, errors = {}, []
    for key, value in (updates or {}).items():
        if key == "quiz_order":
            if value in (ORDER_SEQUENTIAL, ORDER_RANDOM):
                cleaned[key] = value
            else:
                errors.append((key, "sequential か random を指定してください"))
        elif key in ("quiz_color_a", "quiz_color_b"):
            if value in BUTTON_COLORS:
                cleaned[key] = value
            else:
                errors.append((key, "blue / yellow / green / white / red のいずれかを指定してください"))
        elif key in _RANGES:
            lo, hi = _RANGES[key]
            try:
                n = int(float(value))
            except (TypeError, ValueError):
                errors.append((key, "整数で指定してください"))
                continue
            if lo <= n <= hi:
                cleaned[key] = n
            else:
                errors.append((key, f"{lo}〜{hi}の範囲で指定してください"))
        elif key in QUIZ_KEY_SETTINGS:
            try:
                n = int(value)
            except (TypeError, ValueError):
                errors.append((key, "キーコードは整数で指定してください"))
                continue
            if 0 <= n <= 2 ** 31 - 1:
                cleaned[key] = n
            else:
                errors.append((key, "キーコードの範囲が正しくありません"))
        elif key in QUIZ_KEY_NAME_SETTINGS:
            cleaned[key] = str(value or "").strip()[:30]
        else:
            errors.append((key, "未知の設定項目です"))
    return cleaned, errors


def _clean_text(v):
    return str(v).strip()[:MAX_TEXT_LEN] if v else ""


def _clean_part(d):
    """{"text","image"}形式の要素を整える。"""
    d = d if isinstance(d, dict) else {}
    return {"text": _clean_text(d.get("text")), "image": _clean_text(d.get("image"))}


def _has_content(part):
    return bool(part["text"] or part["image"])


def validate_question(q):
    """問題1件を検証し、(cleaned, error)を返す。errorはNoneなら成功。
    問題文・各選択肢は「テキストのみ/画像のみ/併用」のいずれでもよいが、
    どちらも空は不可。解説は任意。"""
    if not isinstance(q, dict):
        return None, "問題の形式が正しくありません"
    question = _clean_part(q.get("question"))
    if not _has_content(question):
        return None, "問題文（テキストか画像）を入力してください"
    choices = q.get("choices")
    if not isinstance(choices, list) or len(choices) != 2:
        return None, "選択肢はA・Bの2つが必要です"
    choices = [_clean_part(c) for c in choices]
    for label, c in zip("AB", choices):
        if not _has_content(c):
            return None, f"選択肢{label}（テキストか画像）を入力してください"
    answer = q.get("answer")
    if answer not in (0, 1):
        return None, "正解はAかBで指定してください"
    qid = _clean_text(q.get("id")) or "q_" + os.urandom(4).hex()
    return {
        "id": qid,
        "question": question,
        "choices": choices,
        "answer": answer,
        "explanation": _clean_part(q.get("explanation")),
    }, None


class QuizStore:
    """問題データと集計のファイル保存。スレッドセーフ（Web側とpygame側から使われる）。"""

    def __init__(self, folder):
        self.folder = folder
        self._lock = threading.RLock()
        self._pending = 0
        os.makedirs(folder, exist_ok=True)

    def _qpath(self):
        return os.path.join(self.folder, QUESTIONS_FILENAME)

    def _spath(self):
        return os.path.join(self.folder, STATS_FILENAME)

    # ---- 問題 ----
    def load_questions(self):
        with self._lock:
            data = load_json_file(self._qpath(), [])
        if not isinstance(data, list):
            return []
        out = []
        for q in data:
            cleaned, err = validate_question(q)
            if cleaned and not err:
                out.append(cleaned)
        return out

    def modify_questions(self, fn):
        """問題一覧を「読む→fnで変更→保存」まで排他的に行う（Webの同時操作で
        片方の変更が消えないように）。fnは問題リストを受け取り、新しいリストを返す。"""
        with self._lock:
            new = fn(self.load_questions())
            self.save_questions(new)
            return new

    def save_questions(self, questions):
        """検証済みの問題リストを保存する。不正な問題が混じっていればValueError。"""
        if len(questions) > MAX_QUESTIONS:
            raise ValueError(f"問題は最大{MAX_QUESTIONS}問までです")
        cleaned_list = []
        for i, q in enumerate(questions):
            cleaned, err = validate_question(q)
            if err:
                raise ValueError(f"{i + 1}問目: {err}")
            cleaned_list.append(cleaned)
        ids = [q["id"] for q in cleaned_list]
        if len(set(ids)) != len(ids):
            raise ValueError("問題IDが重複しています")
        with self._lock:
            _atomic_write_json(self._qpath(), cleaned_list)

    def has_questions(self):
        return len(self.load_questions()) > 0

    # ---- 集計 ----
    def load_stats(self):
        with self._lock:
            data = load_json_file(self._spath(), {})
        return data if isinstance(data, dict) else {}

    def record_answer(self, qid, choice):
        """回答を1件記録する。STATS_FLUSH_EVERY件たまるまでメモリ上に保持し、
        SDカードへの書き込み回数を抑える。flush()で強制的に書き出せる。"""
        if choice not in (0, 1):
            return
        with self._lock:
            if not hasattr(self, "_stats_cache"):
                self._stats_cache = self.load_stats()
            entry = self._stats_cache.setdefault(qid, [0, 0])
            entry[choice] += 1
            self._pending += 1
            if self._pending >= STATS_FLUSH_EVERY:
                self._flush_locked()

    def _flush_locked(self):
        if hasattr(self, "_stats_cache") and self._pending:
            _atomic_write_json(self._spath(), self._stats_cache)
        self._pending = 0

    def flush(self):
        with self._lock:
            self._flush_locked()

    def get_counts(self, qid):
        """(Aの回答数, Bの回答数)。未flushの分も含む。"""
        with self._lock:
            stats = getattr(self, "_stats_cache", None) or self.load_stats()
            a, b = stats.get(qid, [0, 0])
            return int(a), int(b)

    def reset_stats(self, qid=None):
        """集計をリセットする。qid省略で全問題。"""
        with self._lock:
            stats = getattr(self, "_stats_cache", None) or self.load_stats()
            if qid is None:
                stats = {}
            else:
                stats.pop(qid, None)
            self._stats_cache = stats
            self._pending = 1
            self._flush_locked()


_shared_stores = {}
_shared_lock = threading.Lock()


def get_shared_store(folder):
    """フォルダごとに1つだけQuizStoreを作って共有する。サイネージ本体(pygame)とWebサーバーは
    同一プロセスで動くため、同じインスタンスを使えば、回答集計のメモリ上のキャッシュと
    Webからのリセット操作がずれない（別々に持つと、古いキャッシュで上書きしてしまう）。"""
    key = os.path.abspath(folder)
    with _shared_lock:
        if key not in _shared_stores:
            _shared_stores[key] = QuizStore(folder)
        return _shared_stores[key]


def question_images(q):
    """問題が使っている画像ファイル名の集合。"""
    names = {q["question"]["image"], q["explanation"]["image"]}
    names.update(c["image"] for c in q["choices"])
    names.discard("")
    return names


def percentages(a, b):
    """(Aの割合%, Bの割合%)。合計100になるよう丸める。回答0件なら(0, 0)。"""
    total = a + b
    if total == 0:
        return 0, 0
    pa = round(a * 100 / total)
    return pa, 100 - pa


class QuizSession:
    """出題→(回答)→結果・解説→次の問題 の状態遷移と、1人分（1ラウンド）の成績管理。

    press(choice, now): 本体の押しボタン（0=A, 1=B）が押された時に呼ぶ。
      出題中は回答として扱い、結果表示へ移る。結果表示中・成績発表中は、
      どのボタンでも先へ進む（待機時間なし。チャタリング防止の0.3秒だけ無視する）。
    tick(now): 毎フレーム呼ぶ。無操作タイムアウトで先へ進める。
    hold_reset(now): 回答ボタン長押し・本体メニューからの「最初から」。

    ラウンド成績（round_answered / round_correct）は「今の1人分」の記録で、
    リセットしても来場者全員分の蓄積集計（QuizStore）には影響しない。
    リセットされる場面は3つ: ①出題中に誰も答えず時間切れ（前の人はやめたと見なす）
    ②hold_reset()（長押し・メニュー）③ラウンドを最後まで終えて自動的に次の人へ。
    """

    def __init__(self, store, settings=None, rng=None, now=None):
        self.store = store
        self.settings = dict(QUIZ_DEFAULTS)
        self.settings.update(settings or {})
        self.rng = rng or random.Random()
        self.questions = []
        self._bag = []
        self._last_id = None
        self.current = None
        self.state = STATE_QUESTION
        self.selected = None
        self.round_answered = 0
        self.round_correct = 0
        self.last_event_time = 0.0
        self.answered_time = 0.0
        # 直近の切替の理由（画面側の演出用）: "start" / "next"(ボタンで次へ) /
        # "result_idle"(解説後に無操作) / "skipped"(無回答のまま問題だけ入れ替え) /
        # "round_end"(成績発表へ) / "round_restart"(次の人のラウンド開始) /
        # "abandoned"(途中でやめたと見なして最初から) / "reset"(長押し・メニューで最初から)
        self.last_transition = "start"
        self.reload(now if now is not None else time.time())

    def reload(self, now):
        """問題一覧を読み直し、最初の問題から始める（モード突入時・Web更新時）。"""
        self.questions = self.store.load_questions()
        self._restart(now, "start")

    # ---- 出題順 ----
    def _next_question(self):
        if not self.questions:
            return None
        if self.settings["quiz_order"] == ORDER_RANDOM:
            if not self._bag:
                self._bag = list(self.questions)
                self.rng.shuffle(self._bag)
                # 袋の切れ目で同じ問題が連続しないようにする
                if len(self._bag) > 1 and self._bag[-1]["id"] == self._last_id:
                    self._bag[0], self._bag[-1] = self._bag[-1], self._bag[0]
            return self._bag.pop()
        ids = [x["id"] for x in self.questions]
        idx = ids.index(self._last_id) + 1 if self._last_id in ids else 0
        return self.questions[idx % len(self.questions)]

    def _advance(self, now, reason="next"):
        """次の問題へ進む。reason="skipped"（出題中に誰も答えず時間切れ）の場合は、
        正解や集計を一切出さずに問題だけ入れ替え、答えられなかった問題は
        また後で出題されるようにする。
          ・登録順: 順番通りに進むので、一巡後にまた出る
          ・ランダム: 今回の巡回の残りの中へランダムな位置で戻す（次の1問目にはしない）"""
        prev = self.current
        if (reason == "skipped" and prev is not None
                and self.settings["quiz_order"] == ORDER_RANDOM and self._bag):
            self._bag.insert(self.rng.randrange(len(self._bag)), prev)
        self.current = self._next_question()
        self._last_id = self.current["id"] if self.current else None
        self.state = STATE_QUESTION
        self.selected = None
        self.last_event_time = now
        self.last_transition = reason

    def _restart(self, now, reason):
        """ラウンド成績を消し、出題順も最初（登録順なら1問目、ランダムなら新しい並び）に戻す。"""
        self.round_answered = 0
        self.round_correct = 0
        self._bag = []
        self._last_id = None
        self.current = None
        self._advance(now, reason)

    # ---- ラウンド ----
    @property
    def round_size(self):
        """実際に使うラウンド問題数。登録問題数より多くは出題しない（同じ問題の重複を避ける）。
        0はラウンド無し（成績を数えず、問題が延々と続く）。"""
        n = int(self.settings["quiz_round_size"])
        return min(n, len(self.questions)) if n > 0 else 0

    def round_progress(self):
        """(今答えている問題の番号, ラウンド問題数, これまでの正解数)。
        例: 3問目に答える前なら(3, 5, 正解数)。ラウンド無しなら問題数は0。"""
        size = self.round_size
        number = min(self.round_answered + 1, size) if size else 0
        return number, size, self.round_correct

    def hold_reset(self, now):
        """長押し・メニューからの「最初からやり直し」。どの状態からでも使える。"""
        if self.current is None:
            return False
        self._restart(now, "reset")
        return True

    def shift_time(self, delta):
        """クイズ画面が他の画面（メニュー等）に隠れていた間の時間を、
        無操作タイマー・チャタリング防止から差し引く（隠れている間に時間切れしないように）。"""
        self.last_event_time += delta
        self.answered_time += delta

    @property
    def is_empty(self):
        return self.current is None

    def is_correct(self):
        return (self.state == STATE_RESULT and self.current is not None
                and self.selected == self.current["answer"])

    def result_percentages(self):
        """結果画面用の(A%, B%)。"""
        if not self.current:
            return 0, 0
        a, b = self.store.get_counts(self.current["id"])
        return percentages(a, b)

    def lockout_remaining(self, now):
        """チャタリング防止の残り秒数（ほぼ0）。画面表示には使わない。"""
        if self.state == STATE_QUESTION:
            return 0.0
        return max(0.0, ADVANCE_DEBOUNCE_SECONDS - (now - self.answered_time))

    def _round_finished(self):
        size = self.round_size
        return size > 0 and self.round_answered >= size

    def _finish_round_display(self, now):
        self.state = STATE_ROUND_END
        self.selected = None
        self.answered_time = now      # 成績発表の直後も、チャタリングで飛ばさないようデバウンスを効かせる
        self.last_event_time = now
        self.last_transition = "round_end"

    def _after_result(self, now, reason):
        """結果・解説の次へ。ラウンドを終えたなら成績発表、そうでなければ次の問題。"""
        if self._round_finished():
            self._finish_round_display(now)
        else:
            self._advance(now, reason)

    def _new_round(self, now):
        """成績発表の次へ。成績を消し、出題順は続きから（次の人用）。"""
        self.round_answered = 0
        self.round_correct = 0
        self._advance(now, "round_restart")

    def press(self, choice, now):
        """ボタン押下。状態が変わったらTrueを返す。"""
        if self.current is None or choice not in (0, 1):
            return False
        if self.state == STATE_QUESTION:
            self.selected = choice
            self.state = STATE_RESULT
            self.answered_time = now
            self.last_event_time = now
            self.store.record_answer(self.current["id"], choice)
            if self.round_size:
                self.round_answered += 1
                if choice == self.current["answer"]:
                    self.round_correct += 1
            return True
        if self.lockout_remaining(now) > 0:
            return False
        if self.state == STATE_RESULT:
            self._after_result(now, "next")
        else:
            self._new_round(now)
        return True

    def tick(self, now):
        """無操作タイムアウトの監視。状態が変わったらTrue。"""
        if self.current is None:
            return False
        idle = now - self.last_event_time
        if self.state == STATE_RESULT:
            if idle >= self.settings["quiz_result_idle_seconds"]:
                self._after_result(now, "result_idle")
                return True
            return False
        if self.state == STATE_ROUND_END:
            if idle >= self.settings["quiz_round_end_seconds"]:
                self._new_round(now)
                return True
            return False
        limit = self.settings["quiz_question_idle_seconds"]
        if limit == 0 or idle < limit:
            return False
        if self.round_answered > 0:
            # 途中まで答えた人が戻ってこない＝やめたと見なし、次の人のために最初から
            self._restart(now, "abandoned")
        else:
            self._advance(now, "skipped")
        return True


# ============================================
# 一括取り込み（JSON / CSV）
# Web側(quiz_web.py)から使う。ここはファイルI/Oや画像保存をしない純粋な解析・検証だけ
# （単体テストしやすくするため）。画像の実体の取り扱いはquiz_web側が受け持つ。
# ============================================

import csv as _csv
import unicodedata as _ud

IMPORT_MAX_BYTES = 30 * 1024 * 1024

# CSVの見出し名（表記ゆれを許す）→ 内部キー
_CSV_HEADERS = {
    "question": ("問題", "問題文", "question", "q"),
    "a": ("a", "選択肢a", "choicea", "a選択肢"),
    "b": ("b", "選択肢b", "choiceb", "b選択肢"),
    "answer": ("正解", "答え", "answer", "correct"),
    "explanation": ("解説", "explanation"),
    "question_image": ("問題画像", "questionimage", "問題の画像"),
    "a_image": ("a画像", "選択肢a画像", "aimage", "aの画像"),
    "b_image": ("b画像", "選択肢b画像", "bimage", "bの画像"),
    "explanation_image": ("解説画像", "explanationimage", "解説の画像"),
}
_CSV_POSITIONAL = ("question", "a", "b", "answer", "explanation")
CSV_TEMPLATE_HEADER = ["問題", "A", "B", "正解", "解説", "問題画像", "A画像", "B画像", "解説画像"]


def _norm_header(h):
    return _ud.normalize("NFKC", str(h or "")).strip().lower().replace(" ", "").replace("　", "")


def decode_text(raw):
    """UTF-8（BOM付き可）を優先し、だめならShift_JIS(cp932)として読む（Excel保存のCSV対策）。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードを判別できません。UTF-8で保存し直してください")


def parse_answer(value):
    """A/B・1/2（全角可）を 0/1 に。不正ならNone。"""
    v = _ud.normalize("NFKC", str(value if value is not None else "")).strip().upper()
    if v in ("A", "1"):
        return 0
    if v in ("B", "2"):
        return 1
    return None


def parse_import_json(raw):
    """JSON（問題の配列、または {"questions":[...]}）を問題dictのリストにする。"""
    try:
        data = json.loads(decode_text(raw))
    except (ValueError, TypeError) as e:
        raise ValueError(f"JSONとして読めませんでした（{e}）")
    if isinstance(data, dict):
        data = data.get("questions")
    if not isinstance(data, list):
        raise ValueError("問題の配列（[...]）か、{\"questions\": [...]} の形式にしてください")
    return data


def parse_import_csv(raw):
    """CSVを問題dictのリストにする。1行目が見出しなら見出し名で、無ければ
    「問題,A,B,正解,解説」の列順で読む。空行は無視する。"""
    text = decode_text(raw)
    rows = [r for r in _csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("CSVに行がありません")
    lookup = {}
    for key, names in _CSV_HEADERS.items():
        for n in names:
            lookup[_norm_header(n)] = key
    first = [lookup.get(_norm_header(c)) for c in rows[0]]
    if "question" in first:
        cols = first
        body = rows[1:]
    else:
        cols = list(_CSV_POSITIONAL)
        body = rows
    out = []
    for r in body:
        rec = {}
        for key, cell in zip(cols, r):
            if key:
                rec[key] = cell.strip()
        out.append({
            "question": {"text": rec.get("question", ""), "image": rec.get("question_image", "")},
            "choices": [{"text": rec.get("a", ""), "image": rec.get("a_image", "")},
                        {"text": rec.get("b", ""), "image": rec.get("b_image", "")}],
            "answer": parse_answer(rec.get("answer")),
            "explanation": {"text": rec.get("explanation", ""), "image": rec.get("explanation_image", "")},
        })
    return out


def _too_long(part):
    return isinstance(part, dict) and len(str(part.get("text") or "").strip()) > MAX_TEXT_LEN


def _dup_key(q):
    return (q["question"].get("text", ""), q["question"].get("image", ""),
            tuple((c.get("text", ""), c.get("image", "")) for c in q["choices"]), q["answer"])


def prepare_import(rows, existing, replace=False, image_exists=None):
    """取り込み候補(rows)を検証する。ファイルは一切変更しない。
    戻り値: {"ok": 検証済みの問題リスト, "errors": [(行番号, 理由)], "duplicates": 重複でスキップした数,
             "total_after": 取り込み後の総問題数}
    - 1件でも不正があれば呼び出し側は取り込まない（errorsが空の時だけ取り込む）。
    - IDが既存・他の行と衝突する／無い場合は新しいIDを振る（上書きにはならない）。
    - 問題文・選択肢・正解がすでに登録済み（または取り込み内で重複）の行は重複としてスキップ。
    - image_exists(name)がFalseを返す画像名は、その行をエラーにする。"""
    base = [] if replace else list(existing)
    used_ids = {q["id"] for q in base}
    seen = {_dup_key(q) for q in base}
    ok, errors, dups = [], [], 0
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append((n, "問題の形式が正しくありません"))
            continue
        parts = [row.get("question"), row.get("explanation")] + list(row.get("choices") or [])
        if any(_too_long(p) for p in parts):
            errors.append((n, f"文字数が長すぎます（1か所あたり{MAX_TEXT_LEN}文字まで）"))
            continue
        row = dict(row)
        if row.get("id") in used_ids:
            row["id"] = ""
        cleaned, err = validate_question(row)
        if err:
            errors.append((n, err))
            continue
        missing = [name for name in question_images(cleaned)
                   if os.path.basename(name) != name or (image_exists and not image_exists(name))]
        if missing:
            errors.append((n, f"画像が見つかりません: {missing[0]}"))
            continue
        key = _dup_key(cleaned)
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        while cleaned["id"] in used_ids:
            cleaned["id"] = "q_" + os.urandom(4).hex()
        used_ids.add(cleaned["id"])
        ok.append(cleaned)
    total = len(base) + len(ok)
    if total > MAX_QUESTIONS:
        errors.append((0, f"取り込むと{total}問になり、上限の{MAX_QUESTIONS}問を超えます"))
    return {"ok": ok, "errors": errors, "duplicates": dups, "total_after": total}
