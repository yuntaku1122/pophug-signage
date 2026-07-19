# ============================================
# KitchenCar POP Signage - signage_state.py
#
# アップロードページで「非表示」に切り替えた画像のファイル名を
# images/.hidden.json に保存する。main.py側のスライドショーは
# 定期的にこのファイルを読み直し、非表示にした画像を除外する。
# ============================================

import json
import os
import tempfile
import threading

_lock = threading.Lock()


def _atomic_write_json(path, data):
    """一時ファイルに書き込んでからos.replace()で置き換える。
    書き込み中に電源が落ちても、元のファイルか新しいファイルかのどちらかが
    必ず残る（中途半端に壊れた状態にはならない）。"""
    folder = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
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


# ---------------- 優先表示タグ ----------------
# アップロードページで「優先表示1」「優先表示2」に設定した画像のファイル名を
# images/.priority.json に {ファイル名: "priority1"/"priority2"} の形で保存する。
# main.py側は、通常の画像を一定枚数表示するごとに、優先表示に設定された画像を
# まとめて割り込ませる（店のロゴ・メニュー一覧などを定期的に挟み込む用途）。

PRIORITY_TAGS = ("priority1", "priority2")


def _priority_path(image_folder):
    return os.path.join(image_folder, ".priority.json")


def load_priority(image_folder):
    """{ファイル名: "priority1"/"priority2"} の辞書を返す（タグが無いファイルは含まれない）"""
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
