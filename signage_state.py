# ============================================
# KitchenCar POP Signage - signage_state.py
#
# アップロードページで「非表示」に切り替えた画像のファイル名を
# images/.hidden.json に保存する。main.py側のスライドショーは
# 定期的にこのファイルを読み直し、非表示にした画像を除外する。
# ============================================

import json
import os
import threading

_lock = threading.Lock()


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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(hidden_set), f, ensure_ascii=False)


def toggle_hidden(image_folder, filename):
    """指定ファイルの表示/非表示を反転させ、保存後の集合を返す"""
    hidden = load_hidden(image_folder)
    if filename in hidden:
        hidden.discard(filename)
    else:
        hidden.add(filename)
    save_hidden(image_folder, hidden)
    return hidden
