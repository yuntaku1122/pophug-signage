# ============================================
# KitchenCar POP Signage - signage_state.py
#
# アップロードページで「非表示」に切り替えた画像のファイル名を
# images/.hidden.json に保存する。main.py側のスライドショーは
# 定期的にこのファイルを読み直し、非表示にした画像を除外する。
# ============================================

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time

_lock = threading.Lock()


def _atomic_write_json(path, data, fsync=True):
    """一時ファイルに書き込んでからos.replace()で置き換える。
    書き込み中に電源が落ちても、元のファイルか新しいファイルかのどちらかが
    必ず残る（中途半端に壊れた状態にはならない）。

    fsync=True（既定）は実際にディスクへ書き切ってから置き換える、設定値等の
    重要な状態向けの動作。fsync=Falseは、進捗表示のような「失っても実害の
    無い・かつ短時間に何度も書き込む」用途向けの軽量版で、os.replace()自体の
    原子性は保ったまま、実ディスクへの同期だけを省略する（ext4等では単発の
    fsyncでもその時点の他の未確定書き込みをまとめて同期してしまうことがあり、
    短時間に連発すると他のプロセスのI/Oを巻き込んで詰まらせる恐れがあるため）。"""
    folder = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            if fsync:
                os.fsync(f.fileno())  # OSのバッファに留まらず実際にディスクへ書き切らせる
        os.replace(tmp_path, path)  # 同一ファイルシステム内でのrenameはPOSIX上atomic
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _state_path(image_folder):
    return os.path.join(image_folder, ".hidden.json")


def load_hidden(image_folder):
    """非表示に設定されているファイル名の集合を返す"""
    path = _state_path(image_folder)
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_hidden(image_folder, hidden_set):
    path = _state_path(image_folder)
    with _lock:
        _atomic_write_json(path, sorted(hidden_set))


def toggle_hidden(image_folder, filename):
    """指定ファイルの表示/非表示を反転させ、保存後の集合を返す"""
    hidden = load_hidden(image_folder)
    if filename in hidden:
        hidden.discard(filename)
    else:
        hidden.add(filename)
    save_hidden(image_folder, hidden)
    return hidden


def hidden_mtime(image_folder):
    """状態ファイルの更新時刻だけを返す（存在しなければNone）。
    中身を読まずstat()するだけなので、高頻度に呼んでも軽い。"""
    path = _state_path(image_folder)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ---------------- 固定表示（USB取り込みの入れ替え対象から除外する画像） ----------------
# アップロードページで「固定表示」に設定した画像のファイル名を
# images/.pinned.json に保存する。USB取り込み機能は、取り込み完了時に
# 「今回取り込んだ画像」と「固定表示に設定されている画像」以外を自動的に
# 非表示化するため、店のロゴ等の常時表示したい画像はここに登録しておく。
# ただし固定表示はあくまでUSB取り込みの自動非表示化から保護するだけの
# フラグであり、アップロードページの表示/非表示スイッチによる手動操作は
# 固定表示より常に優先される（手動で非表示にすれば固定表示でも隠れる）。

def _pinned_path(image_folder):
    return os.path.join(image_folder, ".pinned.json")


def load_pinned(image_folder):
    """固定表示に設定されているファイル名の集合を返す"""
    path = _pinned_path(image_folder)
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_pinned(image_folder, pinned_set):
    path = _pinned_path(image_folder)
    with _lock:
        _atomic_write_json(path, sorted(pinned_set))


def toggle_pinned(image_folder, filename):
    """指定ファイルの固定表示/通常を反転させ、保存後の集合を返す"""
    pinned = load_pinned(image_folder)
    if filename in pinned:
        pinned.discard(filename)
    else:
        pinned.add(filename)
    save_pinned(image_folder, pinned)
    return pinned


def pinned_mtime(image_folder):
    """固定表示状態ファイルの更新時刻だけを返す（存在しなければNone）"""
    path = _pinned_path(image_folder)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ---------------- USB取り込み履歴（重複取り込み防止） ----------------
# USBメモリーから取り込み済みの画像を、内容のハッシュ値をキーに
# images/.usb_imported.json に {ハッシュ値: 保存後のファイル名} の形で記録する。
# 同じUSBメモリーを何度も抜き差ししても、同一内容のファイルを重複してコピーしない
# ようにするための仕組み（pophug-usb-importから利用する）。

def _usb_imported_path(image_folder):
    return os.path.join(image_folder, ".usb_imported.json")


def load_usb_imported(image_folder):
    """{ハッシュ値: 保存後のファイル名} の辞書を返す"""
    path = _usb_imported_path(image_folder)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_usb_imported(image_folder, imported_map):
    path = _usb_imported_path(image_folder)
    with _lock:
        _atomic_write_json(path, imported_map)


# ---------------- 固定表示のON/OFFに合わせたファイル名の付け替え ----------------
# USB取り込み機能では、USBメモリー上でファイル名の先頭に「fixed_」を付けておくと
# 固定表示として取り込まれる（README参照）。この命名規則をWeb設定画面からの
# 固定表示ON/OFFでも踏襲し、ファイル名を見ただけでどれが固定表示画像か
# 分かるようにする（USB機能導入前にアップロード済みの画像を後から固定表示に
# した場合など、ファイル名だけでは判別できない、という問題への対応）。

def rename_for_pin_state(image_folder, filename, want_pinned):
    """固定表示のON/OFFに合わせて、ファイル名の先頭のfixed_接頭辞を付け外しする。
    実際に名前が変わった場合は新しいファイル名を、変える必要が無い・変えられ
    なかった場合は元のファイル名をそのまま返す。リネームに伴い、非表示リスト・
    優先表示・USB取り込み履歴に残っている古いファイル名の参照も新しい名前へ
    付け替える（.pinned.json自体は呼び出し元で新しい名前を使って保存すること）。"""
    if want_pinned and not filename.lower().startswith("fixed_"):
        new_name = "fixed_" + filename
    elif not want_pinned and filename.lower().startswith("fixed_"):
        new_name = filename[len("fixed_"):]
        if not new_name:
            return filename  # 接頭辞しか無いような異常なファイル名は触らない
    else:
        return filename  # 既に望む状態の命名になっている

    old_path = os.path.join(image_folder, filename)
    new_path = os.path.join(image_folder, new_name)

    if not os.path.exists(old_path) or os.path.exists(new_path):
        # 元ファイルが無い、または同名ファイルが既にある（衝突）場合は
        # 安全のためリネームせず、元のファイル名のままにする
        return filename

    try:
        os.rename(old_path, new_path)
    except OSError:
        return filename

    hidden = load_hidden(image_folder)
    if filename in hidden:
        hidden.discard(filename)
        hidden.add(new_name)
        save_hidden(image_folder, hidden)

    priority = load_priority(image_folder)
    if filename in priority:
        priority[new_name] = priority.pop(filename)
        save_priority(image_folder, priority)

    imported = load_usb_imported(image_folder)
    changed = False
    for h, saved_name in list(imported.items()):
        if saved_name == filename:
            imported[h] = new_name
            changed = True
    if changed:
        save_usb_imported(image_folder, imported)

    return new_name


# ---------------- 表示設定（トランジション時間など） ----------------
# アップロードページから変更した設定を images/.settings.json に保存する。
# main.py側は定期的にこのファイルを読み直し、実行中の設定を上書きする。

def _settings_path(image_folder):
    return os.path.join(image_folder, ".settings.json")


def load_settings(image_folder, defaults=None):
    """保存済みの設定を返す。ファイルに無いキーはdefaultsの値を使う。"""
    settings = dict(defaults or {})
    path = _settings_path(image_folder)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                settings.update(json.load(f))
        except Exception:
            pass
    return settings


def save_settings(image_folder, updates, defaults=None):
    """設定の一部（updates）だけを更新して保存し、マージ後の全設定を返す"""
    settings = load_settings(image_folder, defaults)
    settings.update(updates)
    path = _settings_path(image_folder)
    with _lock:
        _atomic_write_json(path, settings)
    return settings


def settings_mtime(image_folder):
    """設定ファイルの更新時刻だけを返す（存在しなければNone）"""
    path = _settings_path(image_folder)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# 設定値の妥当な範囲・選択肢（Web設定画面／USB設定ファイルの両方で共有する基準）
VALID_TRANSITION_TYPES = ("fade", "slide_left", "slide_right", "slide_up", "slide_down")
VALID_ROTATIONS = (0, 90, 180, 270)
VALID_IMAGE_FIT_MODES = ("contain", "cover", "stretch")


def validate_settings_updates(raw_values):
    """設定値の候補（文字列または数値。キーはload_settings/save_settingsと同じ
    内部キー名）を受け取り、範囲チェック・丸め込みを行った上で有効な項目だけを
    返す。Web設定画面(upload_server.py)とUSB設定ファイル(pophug-usb-import)の
    両方から呼ばれる、検証ルールの唯一の実装（二重実装による基準のズレを防ぐため）。
    raw_valuesに含まれないキーはvalidにもerrorsにも現れない（そのキーは
    「今回は変更しない」という意味になる）。raw_valuesに含まれていても
    認識できないキーは無視する（呼び出し側でraw_values.keys()と
    valid.keys()|{e[0] for e in errors}の差分を見れば「未知のキー」を検出できる）。
    戻り値: (valid_updates: dict, errors: list[(key, message)])"""
    valid = {}
    errors = []

    if "transition_duration" in raw_values:
        try:
            v = round(max(0.1, min(float(raw_values["transition_duration"]), 5.0)), 1)
            valid["transition_duration"] = v
        except (TypeError, ValueError):
            errors.append(("transition_duration", "数値ではありません（0.1〜5.0の範囲で指定してください）"))

    if "image_interval" in raw_values:
        try:
            v = round(max(2.0, min(float(raw_values["image_interval"]), 60.0)), 1)
            valid["image_interval"] = v
        except (TypeError, ValueError):
            errors.append(("image_interval", "数値ではありません（2.0〜60.0の範囲で指定してください）"))

    if "priority_interval" in raw_values:
        try:
            v = max(1, min(int(float(raw_values["priority_interval"])), 50))
            valid["priority_interval"] = v
        except (TypeError, ValueError):
            errors.append(("priority_interval", "数値ではありません（1〜50の範囲で指定してください）"))

    if "transition_type" in raw_values:
        v = str(raw_values["transition_type"]).strip()
        if v in VALID_TRANSITION_TYPES:
            valid["transition_type"] = v
        else:
            errors.append(("transition_type", f"{'/'.join(VALID_TRANSITION_TYPES)} のいずれかで指定してください"))

    if "image_fit_mode" in raw_values:
        v = str(raw_values["image_fit_mode"]).strip()
        if v in VALID_IMAGE_FIT_MODES:
            valid["image_fit_mode"] = v
        else:
            errors.append(("image_fit_mode", f"{'/'.join(VALID_IMAGE_FIT_MODES)} のいずれかで指定してください"))

    if "rotation" in raw_values:
        try:
            v = int(float(raw_values["rotation"])) % 360
        except (TypeError, ValueError):
            errors.append(("rotation", "数値ではありません（0/90/180/270のいずれかで指定してください）"))
        else:
            if v in VALID_ROTATIONS:
                valid["rotation"] = v
            else:
                errors.append(("rotation", "0/90/180/270のいずれかで指定してください"))

    if "setup_ap_ssid" in raw_values:
        v = str(raw_values["setup_ap_ssid"]).strip()
        if 0 < len(v) <= 32:
            valid["setup_ap_ssid"] = v
        else:
            errors.append(("setup_ap_ssid", "1〜32文字で指定してください"))

    if "setup_ap_password" in raw_values:
        v = str(raw_values["setup_ap_password"])
        if v == "" or (8 <= len(v) <= 63):
            valid["setup_ap_password"] = v
        else:
            errors.append(("setup_ap_password", "空、または8〜63文字で指定してください"))

    return valid, errors


# ---------------- 優先表示タグ ----------------
# アップロードページで「優先表示1」〜「優先表示5」に設定した画像のファイル名を
# images/.priority.json に {ファイル名: "priority1"〜"priority5"} の形で保存する。
# main.py側は、通常の画像を一定枚数表示するごとに、優先表示に設定された画像を
# 数字の若い順（1→2→3→4→5）にまとめて割り込ませる（店のロゴ・メニュー一覧などを
# 定期的に挟み込む用途）。同じ優先度の画像が複数ある場合は、ファイル名の
# 昇順（sorted()の順）で表示される。

PRIORITY_TAGS = ("priority1", "priority2", "priority3", "priority4", "priority5")


def _priority_path(image_folder):
    return os.path.join(image_folder, ".priority.json")


def load_priority(image_folder):
    """{ファイル名: "priority1"〜"priority5"} の辞書を返す（タグが無いファイルは含まれない）"""
    path = _priority_path(image_folder)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 万一ファイルが壊れて不正な値が入っていても無視する
        return {k: v for k, v in data.items() if v in PRIORITY_TAGS}
    except Exception:
        return {}


def save_priority(image_folder, priority_map):
    path = _priority_path(image_folder)
    with _lock:
        _atomic_write_json(path, priority_map)


def set_priority_tag(image_folder, filename, tag):
    """指定ファイルの優先表示タグを設定する。
    tagが None/""/"normal" の場合はタグを削除して「通常」に戻す。
    設定後のpriority_mapを返す。"""
    priority_map = load_priority(image_folder)
    if tag not in PRIORITY_TAGS:
        priority_map.pop(filename, None)
    else:
        priority_map[filename] = tag
    save_priority(image_folder, priority_map)
    return priority_map


def priority_mtime(image_folder):
    """優先表示タグファイルの更新時刻だけを返す（存在しなければNone）"""
    path = _priority_path(image_folder)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ---------------- サイネージ画面への通知（ワンショット） ----------------
# Web側で行った操作（Wi-Fi情報の削除など）を、サイネージ本体の画面にも
# 一時的なバナーとして表示するための仕組み。images/.notice.json に
# {"message": ..., "ts": ...} を書き込み、main.py側はファイルの更新時刻が
# 変わったタイミングだけ検知して数秒間表示する（中身を毎回読み直す訳ではないので軽い）。

def _notice_path(image_folder):
    return os.path.join(image_folder, ".notice.json")


def save_notice(image_folder, message, sticky=False, fullscreen=False, durable=True):
    """サイネージ画面に一時表示するメッセージを保存する。
    sticky=Trueの場合、main.py側は「取り外すまで表示」系の通知として扱う
    （USBメモリー取り込み完了時など）。実際に消すのはclear_notice()を呼んだ時、
    または上限時間（NOTICE_STICKY_MAX_SECONDS）に達した時。
    fullscreen=Trueの場合、main.py側は小さなバナーではなく黒背景に大きな文字の
    専有画面として表示する（USBメモリーに読み込める内容が何も無かった場合など、
    見落とされては困る通知向け）。この場合は時間上限も設けられず、
    clear_notice()が呼ばれるまで（＝USBメモリーが抜かれるまで）表示し続ける。

    durable=False（既定はTrue）を指定すると、実ディスクへのfsyncを省略する。
    USB書き出し中の「N/M枚」のような進捗表示は、短時間に何度も呼ばれる上、
    電源断で最後の1回分を失っても実害が無いため、この軽量版を使うことで
    ext4のfsyncが他の未確定I/O（大量の画像コピー等）を巻き込んで
    システム全体のディスクI/Oを詰まらせる事態を避けられる
    （v4.37.2で、この詰まりが原因のウォッチドッグ強制終了・本体再起動を
    修正した際に追加）。設定変更や取り込み完了などの重要な通知は、
    従来通りdurable=True（既定）のまま実ディスクへ書き切る。"""
    path = _notice_path(image_folder)
    with _lock:
        _atomic_write_json(path, {
            "message": message, "ts": time.time(),
            "sticky": bool(sticky), "fullscreen": bool(fullscreen),
        }, fsync=durable)


def clear_notice(image_folder):
    """表示中の通知を消す（USBメモリー取り外し検知時などに呼ぶ）。
    ファイルが無くても何もしない"""
    path = _notice_path(image_folder)
    with _lock:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def load_notice(image_folder):
    """保存されている通知（{"message":..., "ts":...}）を返す。無ければNone"""
    path = _notice_path(image_folder)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def notice_mtime(image_folder):
    """通知ファイルの更新時刻だけを返す（存在しなければNone）"""
    path = _notice_path(image_folder)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ---------------- ネットワーク状態の即時再判定要求（ワンショット） ----------------
# main.py側の「知っているWi-Fiに繋がっているか」の自動判定は、負荷を抑えるため
# 数十秒〜数分間隔でしか行っていない（STANDALONE_CHECK_INTERVAL）。そのため、
# Web側で保存済みWi-Fi情報を削除するなど接続状態が変わりうる操作をした直後は、
# main.py側のフラグ（standalone_active等）がまだ古い状態のままになる時間帯が
# 生まれてしまう。この間にボタンが押されると、実際には未接続なのに「接続中」
# 前提の画面を出してしまう不具合があったため、そうした操作の直後にこの関数を
# 呼んで images/.network_recheck.json の更新時刻を進めておく。main.py側はこの
# 更新時刻の変化だけを検知して、定期チェックを待たずにその場で再判定する
# （中身は使わない、notice_mtime等と同じワンショット信号のパターン）。

def _network_recheck_path(image_folder):
    return os.path.join(image_folder, ".network_recheck.json")


def request_network_recheck(image_folder):
    """接続状態が変わった可能性がある操作（保存済みWi-Fi情報の削除など）の直後に呼ぶ。
    main.py側に定期チェックを待たない即時の再判定を促すだけで、実際の判定・
    アクセスポイント起動などはmain.py側で行う。"""
    path = _network_recheck_path(image_folder)
    with _lock:
        _atomic_write_json(path, {"ts": time.time()})


def network_recheck_mtime(image_folder):
    """再判定要求ファイルの更新時刻だけを返す（存在しなければNone）"""
    path = _network_recheck_path(image_folder)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ---------------- USBオフラインアップデート（検出・確定待ち状態） ----------------
# ネットワークが無い出先でも、GitHub Releaseのsource code(zip)をそのまま
# USBメモリーのルートに置いておけば、挿した時に検出だけを行い、
# images/.usb_update/pending.zip へコピーしておく（root権限のpophug-usb-import
# が実行。詳細はそちらのコメント参照）。安全のため自動適用はせず、実際の適用は
# 本体ボタンの長押しで明示的に確定してもらう2段階方式にしている。
# main.py側は images/.usb_update_pending.json の更新時刻をポーリングして、
# ボタン長押し時の確定待ち画面・バッジ表示の要否を判断する。

def _usb_update_pending_path(image_folder):
    return os.path.join(image_folder, ".usb_update_pending.json")


def save_usb_update_pending(image_folder, version, zip_path, current_version):
    """USBアップデートパッケージを検出・ステージングした直後にpophug-usb-importから呼ぶ。"""
    data = {
        "version": version,
        "zip_path": zip_path,
        "current_version": current_version,
        "detected_at": time.time(),
    }
    with _lock:
        _atomic_write_json(_usb_update_pending_path(image_folder), data)


def load_usb_update_pending(image_folder):
    """保留中のUSBアップデート情報を返す（無ければNone）。
    main.py側が起動時・定期ポーリングの両方でこれを読み、ボタン長押し時の
    挙動やバッジ表示を切り替える。"""
    path = _usb_update_pending_path(image_folder)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def clear_usb_update_pending(image_folder):
    """適用確定・見送りのどちらの場合でも、保留状態を解除する時に呼ぶ。
    ステージング済みのzip本体（images/.usb_update/以下）も合わせて削除し、
    SDカードの空き容量を圧迫したままにしないようにする。"""
    path = _usb_update_pending_path(image_folder)
    with _lock:
        try:
            os.remove(path)
        except OSError:
            pass
    staging_dir = os.path.join(image_folder, ".usb_update")
    shutil.rmtree(staging_dir, ignore_errors=True)


def usb_update_pending_mtime(image_folder):
    """保留状態ファイルの更新時刻だけを返す（存在しなければNone）。
    ファイルが「作られた」時だけでなく「消された」時（適用確定・見送り後）も
    mtimeがNoneに変わることで検知できる（notice_mtime等と同じパターン）。"""
    path = _usb_update_pending_path(image_folder)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ---------------- USB書き出し機能：合言葉キーの管理 ----------------
# 「今格納されている画像を全部USBメモリーへ書き出したい」という要望に対応する
# 機能で使う、書き出し実行を許可するための合言葉（キー）を管理する。
# Web設定画面（スマホ・PCどちらでも操作可）で発行し、生の値はダウンロード
# 応答（USBに置くテキストファイル）にしか含まれない。本体側
# (images/.export_key.json)にはSHA256ハッシュ値だけを保存する。

def _export_key_path(image_folder):
    return os.path.join(image_folder, ".export_key.json")


def save_export_key(image_folder, raw_key):
    """新しい合言葉を発行する。以前発行した合言葉は上書きされ、以後使えなくなる。"""
    data = {
        "key_hash": hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest(),
        "created_at": time.time(),
    }
    with _lock:
        _atomic_write_json(_export_key_path(image_folder), data)


def load_export_key_info(image_folder):
    """発行済みキーの情報（{"created_at": ...}、ハッシュ値は含まない）を返す。
    Web設定画面での「発行済み/未発行」表示用。未発行、または読み込みに
    失敗した場合はNoneを返す。"""
    path = _export_key_path(image_folder)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "key_hash" not in data:
            return None
        return {"created_at": data.get("created_at")}
    except Exception:
        return None


def verify_export_key(image_folder, raw_key):
    """指定された生の合言葉が、発行済みキーと一致するか確認する。
    未発行の場合や、値が空の場合は常にFalseを返す。"""
    if not raw_key:
        return False
    path = _export_key_path(image_folder)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        stored_hash = data.get("key_hash")
    except Exception:
        return False
    if not stored_hash:
        return False
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest() == stored_hash


def clear_export_key(image_folder):
    """発行済みキーを無効化する（Web設定画面の「キーを無効化する」用）。
    以後、以前発行したUSBメモリーでは書き出しができなくなる。"""
    path = _export_key_path(image_folder)
    with _lock:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


# ---------------- USB書き出し機能：待受け状態（ボタン長押しでアーム） ----------------
# 本体ボタンをEXPORT_HOLD_SECONDS秒以上長押しすると、この「待受け」状態に入る
# （main.py側が管理）。root権限で動くpophug-usb-import（別プロセス）は、USB
# メモリー挿入時にこの状態ファイルを見て、書き出しを試みるかどうかを判断する。
# 一定時間（expires_at）操作が無ければ期限切れとして扱い、main.py側・
# pophug-usb-import側の両方でこのタイムスタンプを尊重する。

def _export_standby_path(image_folder):
    return os.path.join(image_folder, ".export_standby.json")


def save_export_standby(image_folder, expires_at):
    """ボタン長押しで待受けモードに入った直後にmain.pyから呼ぶ。"""
    data = {"armed_at": time.time(), "expires_at": expires_at}
    with _lock:
        _atomic_write_json(_export_standby_path(image_folder), data)


def load_export_standby(image_folder):
    """有効な（期限切れでない）待受け状態を返す。無い、または期限切れの場合は
    Noneを返す（期限切れの場合はついでに状態ファイルも削除し、後始末する）。"""
    path = _export_standby_path(image_folder)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict) or data.get("expires_at", 0) < time.time():
        clear_export_standby(image_folder)
        return None
    return data


def clear_export_standby(image_folder):
    """待受け状態を解除する（書き出し成功時・タイムアウト時・ボタンでの
    キャンセル時のいずれからも呼ばれる）。"""
    path = _export_standby_path(image_folder)
    with _lock:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def export_standby_mtime(image_folder):
    """待受け状態ファイルの更新時刻だけを返す（存在しなければNone）。
    main.py側が、他プロセス(pophug-usb-import)による状態変化
    （書き出し完了時の消去など）を検知するために使う。"""
    path = _export_standby_path(image_folder)
    try:
        return os.path.getmtime(path)
    except OSError:
        return None
