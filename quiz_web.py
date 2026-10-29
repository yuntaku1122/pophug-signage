# ============================================
# KitchenCar POP Signage - quiz_web.py
#
# 2択クイズモードのWeb設定画面（/quiz）とAPI。
# 既存の設定ページ(upload_server.py)の巨大なHTMLには手を入れず、独立したページ＋
# Flaskのルート群として追加する（upload_server.create_app()から register_quiz_routes()を
# 呼ぶだけで有効になる）。
#
# 画面は静的なHTML＋JavaScriptで、データはすべて /quiz/api/... をfetchして描画する。
# 利用者が入力した文字列は textContent / DOM API だけで表示し、innerHTMLには入れない
# （問題文に < や > が含まれても壊れない・スクリプトが実行されない）。
#
# 検証ルールは quiz_engine / signage_state.validate_settings_updates に一元化されており、
# ここでは重複して実装しない（USB設定ファイルからも同じ基準で検証される）。
#
# 注意: QUIZ_PAGEはraw文字列（r"""..."""）。JavaScript中の\nなどをPythonが
# 解釈してしまう事故（プロジェクトの頻出バグ）を避けるため、非rawにしてはいけない。
# ============================================

import io
import os
from datetime import datetime

from flask import Response, request, send_from_directory

import quiz_engine
import signage_state

try:
    from config import (QUIZ_DATA_FOLDER, QUIZ_KEY_A_DEFAULT, QUIZ_KEY_B_DEFAULT)
except ImportError:
    QUIZ_DATA_FOLDER = "./quiz_data"
    QUIZ_KEY_A_DEFAULT, QUIZ_KEY_B_DEFAULT = 102, 106

ALLOWED_IMAGE_EXTS = (".jpg", ".jpeg", ".png")
MAX_IMAGE_BYTES = 15 * 1024 * 1024     # アップロード1枚あたりの上限
MAX_IMAGE_SIDE = 1600                  # 保存時に、長辺をこのピクセル数以下へ縮小する


def _save_image(data, ext, path):
    """アップロードされた画像を保存する。Pillowがあれば、スマホ写真のEXIF向き情報を反映し
    （pygameはEXIFの向きを無視するため、そのままだと横倒しで表示される）、長辺を縮小して
    メモリ使用量を抑える。Pillowが無い環境では元のバイト列をそのまま保存する。
    読み込めない画像（拡張子だけ画像のファイル等）はValueError。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        with open(path, "wb") as f:
            f.write(data)
        return
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception:
        raise ValueError("画像を読み込めませんでした。別の画像を選んでください")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    if ext == ".png":
        img.save(path, "PNG")
    else:
        img.convert("RGB").save(path, "JPEG", quality=88)


def register_quiz_routes(app, image_folder, quiz_folder=None):
    quiz_folder = quiz_folder or QUIZ_DATA_FOLDER
    store = quiz_engine.get_shared_store(quiz_folder)
    img_dir = os.path.join(quiz_folder, "images")
    os.makedirs(img_dir, exist_ok=True)

    setting_defaults = dict(quiz_engine.QUIZ_DEFAULTS, quiz_key_a=0, quiz_key_b=0,
                            quiz_key_a_name="", quiz_key_b_name="")

    def cleanup_images(old_names, questions):
        """old_namesのうち、もうどの問題からも使われていない画像ファイルを削除する。"""
        still_used = set()
        for q in questions:
            still_used |= quiz_engine.question_images(q)
        for name in set(old_names) - still_used:
            try:
                os.remove(os.path.join(img_dir, os.path.basename(name)))
            except OSError:
                pass

    @app.route("/quiz", methods=["GET"])
    def quiz_page():
        return Response(QUIZ_PAGE, mimetype="text/html")

    @app.route("/quiz/img/<path:filename>")
    def quiz_image(filename):
        return send_from_directory(img_dir, os.path.basename(filename))

    @app.route("/quiz/api/state", methods=["GET"])
    def quiz_state():
        questions = []
        for q in store.load_questions():
            a, b = store.get_counts(q["id"])
            item = dict(q)
            item["stats"] = {"a": a, "b": b}
            questions.append(item)
        settings = signage_state.load_settings(image_folder, setting_defaults)
        state_settings = {k: settings[k] for k in setting_defaults}
        detected = signage_state.load_last_detected_key(image_folder) or {}
        call_buttons = [{"label": str(b.get("label", "")), "key": int(b.get("key", 0) or 0)}
                        for b in signage_state.load_call_buttons(image_folder)]
        return {
            "questions": questions,
            "settings": state_settings,
            "max_questions": quiz_engine.MAX_QUESTIONS,
            "default_keys": {"a": QUIZ_KEY_A_DEFAULT, "b": QUIZ_KEY_B_DEFAULT},
            "detected": {"key_code": detected.get("key_code"), "key_name": detected.get("key_name")},
            "call_buttons": [b for b in call_buttons if b["key"]],
        }, 200

    @app.route("/quiz/api/image", methods=["POST"])
    def quiz_image_upload():
        f = request.files.get("file")
        if not f or not f.filename:
            return {"error": "画像が選ばれていません"}, 400
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            return {"error": "JPEGまたはPNGの画像を選んでください"}, 400
        data = f.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return {"error": f"画像が大きすぎます（{MAX_IMAGE_BYTES // (1024 * 1024)}MBまで）"}, 413
        out_ext = ".png" if ext == ".png" else ".jpg"
        name = f"qimg_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}{out_ext}"
        try:
            _save_image(data, out_ext, os.path.join(img_dir, name))
        except ValueError as e:
            return {"error": str(e)}, 400
        return {"name": name}, 200

    @app.route("/quiz/api/question", methods=["POST"])
    def quiz_question_save():
        """問題を1件保存する。idが既存の問題と一致すれば上書き、無ければ末尾に追加。"""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {"error": "問題の形式が正しくありません"}, 400
        parts = [data.get("question"), data.get("explanation")] + list(data.get("choices") or [])
        for p in parts:
            name = p.get("image") if isinstance(p, dict) else ""
            if name and (os.path.basename(str(name)) != name
                         or not os.path.isfile(os.path.join(img_dir, name))):
                return {"error": f"画像が見つかりません: {name}"}, 400
        cleaned, err = quiz_engine.validate_question(data)
        if err:
            return {"error": err}, 400

        old_images = set()

        def apply(qs):
            for i, q in enumerate(qs):
                if q["id"] == cleaned["id"]:
                    old_images.update(quiz_engine.question_images(q))
                    qs[i] = cleaned
                    break
            else:
                qs.append(cleaned)
            return qs

        try:
            new_list = store.modify_questions(apply)
        except ValueError as e:
            return {"error": str(e)}, 400
        cleanup_images(old_images, new_list)
        return {"id": cleaned["id"]}, 200

    @app.route("/quiz/api/question/<qid>/delete", methods=["POST"])
    def quiz_question_delete(qid):
        removed = []

        def apply(qs):
            for q in qs:
                if q["id"] == qid:
                    removed.append(q)
            return [q for q in qs if q["id"] != qid]

        new_list = store.modify_questions(apply)
        if not removed:
            return {"error": "問題が見つかりません"}, 404
        store.reset_stats(qid)
        cleanup_images(quiz_engine.question_images(removed[0]), new_list)
        return {"status": "deleted"}, 200

    @app.route("/quiz/api/question/<qid>/move", methods=["POST"])
    def quiz_question_move(qid):
        data = request.get_json(silent=True) or {}
        direction = data.get("direction")
        if direction not in ("up", "down"):
            return {"error": "direction は up か down です"}, 400
        found = []

        def apply(qs):
            ids = [q["id"] for q in qs]
            if qid not in ids:
                return qs
            found.append(True)
            i = ids.index(qid)
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(qs):
                qs[i], qs[j] = qs[j], qs[i]
            return qs

        store.modify_questions(apply)
        if not found:
            return {"error": "問題が見つかりません"}, 404
        return {"status": "moved"}, 200

    @app.route("/quiz/api/stats/reset", methods=["POST"])
    def quiz_stats_reset_all():
        store.reset_stats()
        return {"status": "reset"}, 200

    @app.route("/quiz/api/question/<qid>/stats/reset", methods=["POST"])
    def quiz_stats_reset_one(qid):
        store.reset_stats(qid)
        return {"status": "reset"}, 200

    @app.route("/quiz/api/settings", methods=["POST"])
    def quiz_settings_save():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = request.form.to_dict()
        raw = {k: data[k] for k in quiz_engine.QUIZ_SETTING_KEYS if k in data}
        if not raw:
            return {"error": "設定項目がありません"}, 400
        updates, errors = signage_state.validate_settings_updates(raw)
        if errors:
            key, reason = errors[0]
            return {"error": f"{key}: {reason}"}, 400
        signage_state.save_settings(image_folder, updates)
        return {k: updates[k] for k in updates}, 200


QUIZ_PAGE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>クイズの管理</title>
<style>
  body { font-family: -apple-system, sans-serif; background:#f5f5f0; margin:0; padding:24px; color:#333; }
  a { color:#228b22; }
  h1 { font-size:20px; color:#228b22; margin:0 0 8px; }
  h2 { font-size:16px; margin:28px 0 4px; }
  .hint { font-size:12px; color:#888; margin:0 0 12px; line-height:1.6; }
  .box { background:#fff; border-radius:12px; padding:16px; box-shadow:0 2px 8px rgba(0,0,0,0.08); margin-bottom:12px; }
  button { padding:12px; background:#228b22; color:#fff; border:none; border-radius:8px; font-size:15px; cursor:pointer; }
  button:disabled { background:#bbb; }
  .btn-block { width:100%; }
  .btn-sub { background:#555; }
  .btn-danger { background:#fff; color:#c0392b; border:1px solid #e0a5a5; }
  .btn-small { padding:6px 10px; font-size:12px; }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .q-head { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:8px; }
  .q-num { font-weight:bold; color:#228b22; }
  .part { display:flex; gap:8px; align-items:center; margin:4px 0; }
  .part img { width:64px; height:48px; object-fit:cover; border-radius:6px; background:#e8e8e2; flex-shrink:0; }
  .qimg { width:100%; max-width:240px; height:auto; max-height:160px; object-fit:contain; display:none; margin:6px 0;
          border-radius:6px; background:#e8e8e2; }
  .part-text { font-size:14px; word-break:break-all; }
  .choice { padding:6px 8px; border-radius:8px; background:#f7f7f2; margin:4px 0; }
  .choice.is-answer { background:#eaf5ea; border:1px solid #228b22; }
  .badge { display:inline-block; min-width:22px; text-align:center; border-radius:50%; color:#fff; font-size:12px; padding:2px 0; flex-shrink:0; }
  .badge-a { background:#2e6ebe; } .badge-b { background:#d65478; }
  .bar { height:10px; border-radius:5px; background:#e8e8e2; overflow:hidden; display:flex; margin:6px 0 2px; }
  .bar-a { background:#2e6ebe; } .bar-b { background:#d65478; }
  .stats-text { font-size:12px; color:#666; }
  .setting-row { margin-top:14px; }
  .setting-row label { font-size:14px; display:block; margin-bottom:4px; }
  .setting-row input, .setting-row select, textarea { width:100%; box-sizing:border-box; padding:10px; border-radius:8px;
        border:1px solid #ccc; font-size:15px; background:#fff; font-family:inherit; }
  .status { font-size:12px; color:#999; min-height:16px; margin-top:6px; }
  .status.is-error { color:#c0392b; }
  .modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:1000;
        align-items:flex-start; justify-content:center; padding:16px; box-sizing:border-box; overflow-y:auto; }
  .modal-overlay.is-open { display:flex; }
  .modal-content { background:#fff; border-radius:14px; padding:18px; width:100%; max-width:520px; box-sizing:border-box; }
  .field { margin-top:14px; }
  .field-title { font-size:14px; font-weight:bold; margin-bottom:4px; }
  .link-btn { background:none; color:#c0392b; padding:2px 0; font-size:12px; text-decoration:underline; }
  .radio-row { display:flex; gap:16px; margin-top:6px; }
  .radio-row label { font-size:15px; display:flex; gap:6px; align-items:center; }
  .radio-row input { width:auto; }
  .key-now { font-size:14px; margin:6px 0; }
  .detected-box { background:#f7f7f2; border-radius:8px; padding:10px; margin:10px 0; }
</style>
</head>
<body>
  <p style="margin:0 0 12px;"><a href="/">← サイネージの設定へ戻る</a></p>

  <div class="box">
    <h1>クイズの管理</h1>
    <p class="hint">押しボタン2個（A・B）で答える2択クイズです。問題を1問以上登録すると、サイネージ本体のメニューに「クイズモードへ」が表示されます（本体ボタンを押す→短押しで項目を選ぶ→長押しで決定）。</p>
  </div>

  <h2>問題一覧</h2>
  <p class="hint">登録した順に出題されます（設定でランダムにもできます）。画像のみ・テキストのみ・両方のどれでも登録できます。</p>
  <div id="q-list"></div>
  <button type="button" id="add-btn" class="btn-block">＋ 問題を追加</button>
  <p class="status" id="list-status"></p>

  <h2>A・Bボタンの割り当て</h2>
  <div class="box">
    <p class="hint">お使いの押しボタンが、AとBのどちらかを決めます。クイズモード中にボタンを押すと、下に「最後に検出されたキー」が表示されるので、AかBに割り当ててください（呼び出し表示ボタンと同じキーを使うこともできます）。</p>
    <div class="key-now" id="key-a-now"></div>
    <div class="key-now" id="key-b-now"></div>
    <div class="detected-box">
      <div class="stats-text">最後に検出されたキー</div>
      <div id="detected-key" style="font-size:16px; margin:4px 0;">（まだ検出されていません）</div>
      <div class="row">
        <button type="button" id="assign-a" class="btn-small" disabled>Aに割り当てる</button>
        <button type="button" id="assign-b" class="btn-small" disabled>Bに割り当てる</button>
      </div>
    </div>
    <div id="call-key-list"></div>
    <div class="row" style="margin-top:8px;">
      <button type="button" id="reset-keys" class="btn-small btn-sub">既定（A=F・B=J）に戻す</button>
    </div>
    <p class="status" id="key-status"></p>
  </div>

  <h2>出題と進行の設定</h2>
  <div class="box">
    <div id="settings-form"></div>
    <button type="button" id="save-settings" class="btn-block" style="margin-top:16px;">設定を保存</button>
    <p class="status" id="settings-status"></p>
  </div>

  <h2>回答の集計</h2>
  <div class="box">
    <p class="hint">各問題の「A・Bを選んだ人数」は、問題一覧に表示されます。ここでは全問題の集計をまとめて消せます（1人分の途中成績はサイネージ側で自動的にリセットされるため、ここでは消えません）。</p>
    <button type="button" id="reset-all-stats" class="btn-danger btn-block">全ての集計をリセット</button>
    <p class="status" id="stats-status"></p>
  </div>

  <div class="modal-overlay" id="editor-overlay">
    <div class="modal-content">
      <h1 id="editor-title">問題を追加</h1>
      <div id="editor-body"></div>
      <p class="status" id="editor-status"></p>
      <div class="row" style="margin-top:12px;">
        <button type="button" id="editor-save" style="flex:1;">保存</button>
        <button type="button" id="editor-cancel" class="btn-sub" style="flex:1;">キャンセル</button>
      </div>
    </div>
  </div>

  <script>
  (function () {
    var state = null;
    var detected = { key_code: null, key_name: null };

    function el(tag, props, kids) {
      var e = document.createElement(tag);
      if (props) {
        Object.keys(props).forEach(function (k) {
          var v = props[k];
          if (k === 'text') { e.textContent = v; }
          else if (k === 'class') { e.className = v; }
          else if (k.slice(0, 2) === 'on') { e.addEventListener(k.slice(2), v); }
          else if (v === true) { e.setAttribute(k, ''); }
          else if (v !== false && v !== null && v !== undefined) { e.setAttribute(k, v); }
        });
      }
      (kids || []).forEach(function (c) {
        if (c) { e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); }
      });
      return e;
    }
    function $(id) { return document.getElementById(id); }
    function setStatus(id, msg, isError) {
      var s = $(id);
      s.textContent = msg || '';
      s.className = 'status' + (isError ? ' is-error' : '');
    }
    async function api(path, body) {
      var opts = { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' } };
      opts.body = JSON.stringify(body || {});
      var r = await fetch(path, opts);
      var data = {};
      try { data = await r.json(); } catch (e) { data = {}; }
      if (!r.ok) { throw new Error(data.error || '失敗しました'); }
      return data;
    }

    // ---------- 問題一覧 ----------
    function partKids(part) {
      var kids = [];
      if (part.image) {
        kids.push(el('img', { src: '/quiz/img/' + encodeURIComponent(part.image), alt: '' }));
      }
      if (part.text) { kids.push(el('span', { class: 'part-text', text: part.text })); }
      if (!kids.length) { kids.push(el('span', { class: 'stats-text', text: '（なし）' })); }
      return kids;
    }

    function renderList() {
      var list = $('q-list');
      list.textContent = '';
      var qs = state.questions;
      if (!qs.length) {
        list.appendChild(el('div', { class: 'box' }, [el('p', { class: 'hint', text: 'まだ問題がありません。「＋ 問題を追加」から登録してください。' })]));
      }
      qs.forEach(function (q, i) {
        var total = q.stats.a + q.stats.b;
        var pa = total ? Math.round(q.stats.a * 100 / total) : 0;
        var pb = total ? 100 - pa : 0;
        var card = el('div', { class: 'box' });
        card.appendChild(el('div', { class: 'q-head' }, [
          el('span', { class: 'q-num', text: '第' + (i + 1) + '問' }),
          el('div', { class: 'row' }, [
            el('button', { type: 'button', class: 'btn-small btn-sub', text: '↑', disabled: i === 0, onclick: function () { move(q.id, 'up'); } }),
            el('button', { type: 'button', class: 'btn-small btn-sub', text: '↓', disabled: i === qs.length - 1, onclick: function () { move(q.id, 'down'); } }),
            el('button', { type: 'button', class: 'btn-small', text: '編集', onclick: function () { openEditor(q); } }),
            el('button', { type: 'button', class: 'btn-small btn-danger', text: '削除', onclick: function () { removeQuestion(q, i); } })
          ])
        ]));
        card.appendChild(el('div', { class: 'part' }, partKids(q.question)));
        ['A', 'B'].forEach(function (letter, ci) {
          var c = el('div', { class: 'choice' + (q.answer === ci ? ' is-answer' : '') });
          var badge = el('span', { class: 'badge ' + (ci === 0 ? 'badge-a' : 'badge-b'), text: letter });
          c.appendChild(el('div', { class: 'part' }, [badge].concat(partKids(q.choices[ci]))));
          if (q.answer === ci) { c.appendChild(el('div', { class: 'stats-text', text: '← 正解' })); }
          card.appendChild(c);
        });
        if (q.explanation.text || q.explanation.image) {
          card.appendChild(el('div', { class: 'stats-text', text: '解説：' }));
          card.appendChild(el('div', { class: 'part' }, partKids(q.explanation)));
        }
        card.appendChild(el('div', { class: 'bar' }, [
          el('div', { class: 'bar-a', style: 'width:' + pa + '%' }),
          el('div', { class: 'bar-b', style: 'width:' + pb + '%' })
        ]));
        card.appendChild(el('div', { class: 'row', style: 'justify-content:space-between;' }, [
          el('span', { class: 'stats-text', text: total ? ('A ' + q.stats.a + '人（' + pa + '%） / B ' + q.stats.b + '人（' + pb + '%）') : 'まだ回答がありません' }),
          total ? el('button', { type: 'button', class: 'link-btn', text: 'この問題の集計をリセット', onclick: function () { resetStats(q.id); } }) : null
        ]));
        list.appendChild(card);
      });
      $('add-btn').disabled = qs.length >= state.max_questions;
    }

    async function move(id, direction) {
      try { await api('/quiz/api/question/' + encodeURIComponent(id) + '/move', { direction: direction }); await load(); }
      catch (e) { setStatus('list-status', e.message, true); }
    }
    async function removeQuestion(q, i) {
      if (!confirm('第' + (i + 1) + '問を削除します。この問題の集計も消えます。よろしいですか？')) { return; }
      try { await api('/quiz/api/question/' + encodeURIComponent(q.id) + '/delete'); await load(); setStatus('list-status', '削除しました'); }
      catch (e) { setStatus('list-status', e.message, true); }
    }
    async function resetStats(id) {
      if (!confirm('この問題の集計をリセットします。よろしいですか？')) { return; }
      try { await api('/quiz/api/question/' + encodeURIComponent(id) + '/stats/reset'); await load(); }
      catch (e) { setStatus('list-status', e.message, true); }
    }

    // ---------- 問題エディタ ----------
    var editorFields = null;
    var editorId = null;

    function partField(title, part, rows) {
      var st = { image: (part && part.image) || '', file: null };
      var ta = el('textarea', { rows: rows || 2, maxlength: 200, placeholder: 'テキスト（空欄でもOK。画像だけでも登録できます）' });
      ta.value = (part && part.text) || '';
      var preview = el('img', { class: 'qimg', alt: '' });
      var fileIn = el('input', { type: 'file', accept: 'image/png,image/jpeg', style: 'margin-top:6px;' });
      var rm = el('button', { type: 'button', class: 'link-btn', text: '画像を外す' });
      function refresh() {
        var has = !!(st.file || st.image);
        if (st.file) { preview.src = URL.createObjectURL(st.file); }
        else if (st.image) { preview.src = '/quiz/img/' + encodeURIComponent(st.image); }
        preview.style.display = has ? 'block' : 'none';
        rm.style.display = has ? 'inline' : 'none';
      }
      rm.addEventListener('click', function () { st.image = ''; st.file = null; fileIn.value = ''; refresh(); });
      fileIn.addEventListener('change', function () { st.file = fileIn.files[0] || null; refresh(); });
      refresh();
      return {
        root: el('div', { class: 'field' }, [el('div', { class: 'field-title', text: title }), ta, preview, rm, fileIn]),
        get: async function () {
          var image = st.image;
          if (st.file) {
            var fd = new FormData();
            fd.append('file', st.file);
            var r = await fetch('/quiz/api/image', { method: 'POST', body: fd });
            var d = {};
            try { d = await r.json(); } catch (e) { d = {}; }
            if (!r.ok) { throw new Error(d.error || '画像のアップロードに失敗しました'); }
            image = d.name;
          }
          return { text: ta.value.trim(), image: image };
        }
      };
    }

    function openEditor(q) {
      editorId = q ? q.id : null;
      $('editor-title').textContent = q ? '問題を編集' : '問題を追加';
      var body = $('editor-body');
      body.textContent = '';
      var f = {
        question: partField('問題', q && q.question, 3),
        a: partField('選択肢 A', q && q.choices[0], 2),
        b: partField('選択肢 B', q && q.choices[1], 2),
        explanation: partField('解説（任意。答えを見せた後に表示されます）', q && q.explanation, 3)
      };
      var answer = q ? q.answer : 0;
      var radioA = el('input', { type: 'radio', name: 'q-answer', value: '0' });
      var radioB = el('input', { type: 'radio', name: 'q-answer', value: '1' });
      radioA.checked = (answer === 0);
      radioB.checked = (answer === 1);
      f.radioA = radioA; f.radioB = radioB;
      body.appendChild(f.question.root);
      body.appendChild(f.a.root);
      body.appendChild(f.b.root);
      body.appendChild(el('div', { class: 'field' }, [
        el('div', { class: 'field-title', text: '正解はどちら？' }),
        el('div', { class: 'radio-row' }, [
          el('label', null, [radioA, 'A']),
          el('label', null, [radioB, 'B'])
        ])
      ]));
      body.appendChild(f.explanation.root);
      editorFields = f;
      setStatus('editor-status', '');
      $('editor-overlay').classList.add('is-open');
    }
    function closeEditor() { $('editor-overlay').classList.remove('is-open'); editorFields = null; }

    async function saveEditor() {
      var f = editorFields;
      if (!f) { return; }
      var btn = $('editor-save');
      btn.disabled = true;
      setStatus('editor-status', '保存中...（画像があると少し時間がかかります）');
      try {
        var payload = {
          question: await f.question.get(),
          choices: [await f.a.get(), await f.b.get()],
          answer: f.radioB.checked ? 1 : 0,
          explanation: await f.explanation.get()
        };
        if (editorId) { payload.id = editorId; }
        await api('/quiz/api/question', payload);
        closeEditor();
        await load();
        setStatus('list-status', '保存しました。サイネージには数秒で反映されます');
      } catch (e) {
        setStatus('editor-status', e.message, true);
      } finally {
        btn.disabled = false;
      }
    }

    // ---------- A・Bボタンの割り当て ----------
    function keyLabel(code, name, defName) {
      if (code) { return (name || 'キー') + '（コード ' + code + '）'; }
      return '未設定（既定: ' + defName + '）';
    }
    function renderKeys() {
      var s = state.settings;
      $('key-a-now').textContent = 'A：' + keyLabel(s.quiz_key_a, s.quiz_key_a_name, 'F');
      $('key-b-now').textContent = 'B：' + keyLabel(s.quiz_key_b, s.quiz_key_b_name, 'J');
      var box = $('call-key-list');
      box.textContent = '';
      if (state.call_buttons.length) {
        box.appendChild(el('div', { class: 'stats-text', text: '呼び出し表示ボタンのキーから選ぶ' }));
        state.call_buttons.forEach(function (b) {
          box.appendChild(el('div', { class: 'row', style: 'margin:4px 0;' }, [
            el('span', { class: 'part-text', text: b.label + '（コード ' + b.key + '）' }),
            el('button', { type: 'button', class: 'btn-small', text: 'Aに', onclick: function () { assignKey('a', b.key, b.label + 'のボタン'); } }),
            el('button', { type: 'button', class: 'btn-small', text: 'Bに', onclick: function () { assignKey('b', b.key, b.label + 'のボタン'); } })
          ]));
        });
      }
    }
    function renderDetected() {
      var has = detected.key_code !== null && detected.key_code !== undefined;
      $('detected-key').textContent = has ? ((detected.key_name || 'キー') + '（コード ' + detected.key_code + '）') : '（まだ検出されていません）';
      $('assign-a').disabled = !has;
      $('assign-b').disabled = !has;
    }
    async function assignKey(which, code, name) {
      var body = {};
      body['quiz_key_' + which] = code;
      body['quiz_key_' + which + '_name'] = name;
      try { await api('/quiz/api/settings', body); await load(); setStatus('key-status', which.toUpperCase() + 'に割り当てました'); }
      catch (e) { setStatus('key-status', e.message, true); }
    }
    async function pollDetected() {
      try {
        var r = await fetch('/last-detected-key');
        if (r.ok) { detected = await r.json(); renderDetected(); }
      } catch (e) { /* 通信できない間は何もしない */ }
    }

    // ---------- 設定フォーム ----------
    var SETTING_FIELDS = [
      { key: 'quiz_order', label: '出題の順番', type: 'select', options: [['sequential', '登録した順'], ['random', 'ランダム（一巡するまで同じ問題は出ません）']] },
      { key: 'quiz_round_size', label: '1人分の問題数（0にすると成績を数えず、問題が続きます）', min: 0, max: 50 },
      { key: 'quiz_result_idle_seconds', label: '解説を表示してから、操作が無くても次の問題へ進むまでの秒数', min: 5, max: 600 },
      { key: 'quiz_question_idle_seconds', label: '出題中に誰も答えない時、次の問題に替えるまでの秒数（0で替えない）', min: 0, max: 600 },
      { key: 'quiz_answer_lockout_seconds', label: '回答した直後、ボタンを受け付けない秒数（連打防止）', min: 0, max: 10 },
      { key: 'quiz_round_end_seconds', label: '成績発表から次の人へ進むまでの秒数', min: 5, max: 600 },
      { key: 'quiz_hold_reset_seconds', label: 'ボタン長押しで最初からやり直す秒数', min: 1, max: 10 }
    ];
    function renderSettings() {
      var form = $('settings-form');
      form.textContent = '';
      SETTING_FIELDS.forEach(function (f) {
        var input;
        if (f.type === 'select') {
          input = el('select', { id: 'set-' + f.key }, f.options.map(function (o) { return el('option', { value: o[0], text: o[1] }); }));
        } else {
          input = el('input', { id: 'set-' + f.key, type: 'number', min: f.min, max: f.max, step: '1', inputmode: 'numeric' });
        }
        input.value = state.settings[f.key];
        form.appendChild(el('div', { class: 'setting-row' }, [el('label', { for: 'set-' + f.key, text: f.label }), input]));
      });
    }
    async function saveSettings() {
      var body = {};
      SETTING_FIELDS.forEach(function (f) { body[f.key] = $('set-' + f.key).value; });
      try { await api('/quiz/api/settings', body); setStatus('settings-status', '保存しました。サイネージには数秒で反映されます'); await load(); }
      catch (e) { setStatus('settings-status', e.message, true); }
    }

    async function load() {
      var r = await fetch('/quiz/api/state');
      state = await r.json();
      renderList();
      renderKeys();
      renderSettings();
    }

    $('add-btn').addEventListener('click', function () { openEditor(null); });
    $('editor-save').addEventListener('click', saveEditor);
    $('editor-cancel').addEventListener('click', closeEditor);
    $('save-settings').addEventListener('click', saveSettings);
    $('assign-a').addEventListener('click', function () { assignKey('a', detected.key_code, detected.key_name || ''); });
    $('assign-b').addEventListener('click', function () { assignKey('b', detected.key_code, detected.key_name || ''); });
    $('reset-keys').addEventListener('click', async function () {
      try { await api('/quiz/api/settings', { quiz_key_a: 0, quiz_key_b: 0, quiz_key_a_name: '', quiz_key_b_name: '' }); await load(); setStatus('key-status', '既定に戻しました'); }
      catch (e) { setStatus('key-status', e.message, true); }
    });
    $('reset-all-stats').addEventListener('click', async function () {
      if (!confirm('全ての問題の集計をリセットします。よろしいですか？')) { return; }
      try { await api('/quiz/api/stats/reset'); await load(); setStatus('stats-status', 'リセットしました'); }
      catch (e) { setStatus('stats-status', e.message, true); }
    });

    load().catch(function () { setStatus('list-status', '読み込みに失敗しました。ページを再読み込みしてください', true); });
    pollDetected();
    setInterval(pollDetected, 2000);
  })();
  </script>
</body>
</html>
"""
