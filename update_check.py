# ============================================
# KitchenCar POP Signage - update_check.py
#
# GitHub Releasesを更新配信先として使い、バージョン確認・ダウンロード・
# ファイル入れ替え・簡易動作確認・失敗時のロールバックまでを行う。
# root権限が必要な最後の仕上げ（pophug-netctlの入れ替え・サービス再起動）
# だけは /usr/local/bin/pophug-update-apply をsudo経由で呼び出す。
# ============================================

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request

from version import __version__

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATE_APPLY_PATH = "/usr/local/bin/pophug-update-apply"

# 更新時に上書きしない（お客さんごとのデータ・環境なので温存する）もの
PRESERVE = {"images", "venv", ".git", "__pycache__"}


def _parse_version(v):
    """'v4.3.0' や '4.3.0' のような文字列を (4, 3, 0) のタプルに変換する"""
    v = v.lstrip("vV").strip()
    parts = []
    for p in v.split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_latest(repo, timeout=10):
    """GitHub Releases APIから最新バージョン情報を取得する。
    repo は "ユーザー名/リポジトリ名" の形式。"""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": "リリースがまだ公開されていません"}
        return {"error": f"HTTPエラー: {e.code}"}
    except Exception as e:
        return {"error": f"通信エラー: {e}"}

    tag = data.get("tag_name", "")
    latest_tuple = _parse_version(tag)
    current_tuple = _parse_version(__version__)

    return {
        "current_version": __version__,
        "latest_version": tag.lstrip("vV"),
        "update_available": latest_tuple > current_tuple,
        "changelog": (data.get("body") or "").strip(),
        "tarball_url": data.get("tarball_url"),
        "published_at": data.get("published_at"),
    }


def _extract_single_dir(archive_path, extract_to):
    """GitHubのソースアーカイブ(tar.gz)を展開し、中の唯一のトップフォルダのパスを返す"""
    with tarfile.open(archive_path) as tf:
        # パストラバーサル対策: '..'を含むメンバーが無いことを確認してから展開する
        for member in tf.getmembers():
            if member.name.startswith("/") or ".." in member.name.split("/"):
                raise ValueError(f"不正なパスを含むアーカイブです: {member.name}")
        tf.extractall(extract_to)

    entries = [e for e in os.listdir(extract_to) if not e.startswith(".")]
    if len(entries) != 1:
        raise ValueError(f"展開されたフォルダの構成が想定と異なります: {entries}")
    return os.path.join(extract_to, entries[0])


def apply_update(tarball_url, target_version, timeout=60):
    """新バージョンをダウンロードして適用する。
    戻り値は (成功したか, メッセージ) のタプル。"""
    if not tarball_url:
        return False, "ダウンロードURLが取得できませんでした"

    with tempfile.TemporaryDirectory(prefix="pophug-update-") as tmp:
        archive_path = os.path.join(tmp, "release.tar.gz")
        try:
            req = urllib.request.Request(tarball_url, headers={"User-Agent": "pophug-update"})
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(archive_path, "wb") as f:
                shutil.copyfileobj(resp, f)
        except Exception as e:
            return False, f"ダウンロードに失敗しました: {e}"

        extract_dir = os.path.join(tmp, "extracted")
        os.makedirs(extract_dir)
        try:
            src_dir = _extract_single_dir(archive_path, extract_dir)
        except Exception as e:
            return False, f"アーカイブの展開に失敗しました: {e}"

        backup_dir = os.path.join(APP_DIR, f".backup-{__version__}")
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
        os.makedirs(backup_dir)

        moved = []
        try:
            # 既存ファイルを一旦バックアップへ退避（images/やvenv/は温存）
            for name in os.listdir(APP_DIR):
                if name in PRESERVE or name.startswith(".backup-"):
                    continue
                src = os.path.join(APP_DIR, name)
                dst = os.path.join(backup_dir, name)
                shutil.move(src, dst)
                moved.append(name)

            # 新しいファイルを配置
            for name in os.listdir(src_dir):
                if name in PRESERVE:
                    continue
                shutil.move(os.path.join(src_dir, name), os.path.join(APP_DIR, name))

            # 依存パッケージを更新（requirements.txtに変更が無ければ実質何もしない）
            pip_path = os.path.join(APP_DIR, "venv", "bin", "pip")
            req_path = os.path.join(APP_DIR, "requirements.txt")
            if os.path.exists(pip_path) and os.path.exists(req_path):
                subprocess.run(
                    [pip_path, "install", "-r", req_path],
                    capture_output=True, text=True, timeout=180,
                )

            # 新しいコードが最低限壊れていないか確認する
            python_path = os.path.join(APP_DIR, "venv", "bin", "python3")
            main_path = os.path.join(APP_DIR, "main.py")
            check = subprocess.run(
                [python_path, "-m", "py_compile", main_path],
                capture_output=True, text=True, timeout=30,
            )
            if check.returncode != 0:
                raise RuntimeError(f"新しいコードの検証に失敗しました: {check.stderr}")

        except Exception as e:
            _rollback(backup_dir, moved)
            return False, f"{e}（元のバージョンに戻しました）"

        # root権限が必要な仕上げ（pophug-netctlの入れ替え・サービス再起動）
        try:
            r = subprocess.run(
                ["sudo", UPDATE_APPLY_PATH, APP_DIR],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            _rollback(backup_dir, moved)
            return False, f"仕上げ処理の実行に失敗しました: {e}（元のバージョンに戻しました）"

        if r.returncode != 0:
            _rollback(backup_dir, moved)
            return False, f"仕上げ処理に失敗しました: {r.stderr}（元のバージョンに戻しました）"

        return True, f"v{target_version} への更新を適用しました。再起動しています..."


def _rollback(backup_dir, moved_names):
    """更新失敗時に、バックアップしておいた元のファイルを復元する"""
    for name in moved_names:
        src = os.path.join(backup_dir, name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(APP_DIR, name)
        if os.path.exists(dst):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            else:
                os.remove(dst)
        shutil.move(src, dst)
