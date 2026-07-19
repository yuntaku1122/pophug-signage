# ============================================
# KitchenCar POP Signage - update_check.py
#
# GitHub Releasesを更新配信先として使い、バージョン確認・ダウンロード・
# ファイル入れ替え・簡易動作確認・失敗時のロールバックまでを行う。
# root権限が必要な最後の仕上げ（pophug-netctlの入れ替え・サービス再起動）
# だけは /usr/local/bin/pophug-update-apply をsudo経由で呼び出す。
#
# 重要な設計上の注意:
# 「仕上げ処理」はこのプロセス自身が動いているsystemdサービスを再起動させる。
# 再起動が実際に行われると、それを呼び出しているこのPythonプロセス自体が
# 結果を返す前に強制終了させられる可能性が高い（実際にこれが原因で、
# 「成功したのにWeb側には結果が伝わらない」という不具合が発生した）。
# そのため、進行状況はメモリ上の変数ではなく、必ず状態ファイルに書き込む。
# これによりプロセスが再起動をまたいでも、新しいプロセスが同じファイルを
# 読んで正しい状態をWeb側に返せる。
# ============================================

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import traceback
import urllib.error
import urllib.request

from version import __version__

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATE_APPLY_PATH = "/usr/local/bin/pophug-update-apply"
STATUS_PATH = os.path.join(APP_DIR, "images", ".update_status.json")

# 更新時に上書きしない（お客さんごとのデータ・環境なので温存する）もの
PRESERVE = {"images", "venv", ".git", "__pycache__"}


def log(msg):
    from datetime import datetime
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] [update] {msg}", flush=True)


def _write_status(state, error=None, target_version=None):
    """進行状況をファイルに書き込む。プロセスが再起動で死んでも、
    次のプロセスがこのファイルを読めば正しい状態を継続して報告できる。
    書き込み失敗自体でアップデート処理を止めないよう、例外は握りつぶす。"""
    try:
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        data = {
            "state": state,
            "error": error,
            "version": target_version,
            "updated_at": time.time(),
        }
        tmp_path = STATUS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, STATUS_PATH)
    except Exception as e:
        log(f"状態ファイルの書き込みに失敗しました（無視して続行）: {e}")


def read_status():
    """現在のアップデート進行状況を返す（Web側のポーリングから呼ばれる）"""
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"state": "idle", "error": None, "version": None}


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
    戻り値は (成功したか, メッセージ) のタプル。
    ※このプロセス自身が最後に再起動される可能性があるため、状態は
      戻り値だけでなく、途中経過も逐一 _write_status() でファイルに残す。"""
    log(f"更新処理を開始します: target_version={target_version}, tarball_url={tarball_url}")
    _write_status("applying", target_version=target_version)

    if not tarball_url:
        msg = "ダウンロードURLが取得できませんでした"
        log(f"失敗: {msg}")
        _write_status("failed", error=msg, target_version=target_version)
        return False, msg

    with tempfile.TemporaryDirectory(prefix="pophug-update-") as tmp:
        archive_path = os.path.join(tmp, "release.tar.gz")
        log("ダウンロードを開始します...")
        try:
            req = urllib.request.Request(tarball_url, headers={"User-Agent": "pophug-update"})
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(archive_path, "wb") as f:
                shutil.copyfileobj(resp, f)
            log(f"ダウンロード完了: {os.path.getsize(archive_path)} bytes")
        except Exception as e:
            msg = f"ダウンロードに失敗しました: {e}"
            log(f"失敗: {msg}\n{traceback.format_exc()}")
            _write_status("failed", error=msg, target_version=target_version)
            return False, msg

        extract_dir = os.path.join(tmp, "extracted")
        os.makedirs(extract_dir)
        try:
            src_dir = _extract_single_dir(archive_path, extract_dir)
            log(f"展開完了: {src_dir}")
        except Exception as e:
            msg = f"アーカイブの展開に失敗しました: {e}"
            log(f"失敗: {msg}\n{traceback.format_exc()}")
            _write_status("failed", error=msg, target_version=target_version)
            return False, msg

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
            log(f"既存ファイルの退避完了: {moved}")

            # 新しいファイルを配置
            placed = []
            for name in os.listdir(src_dir):
                if name in PRESERVE:
                    continue
                shutil.move(os.path.join(src_dir, name), os.path.join(APP_DIR, name))
                placed.append(name)
            log(f"新しいファイルの配置完了: {placed}")

            # 依存パッケージを更新（requirements.txtに変更が無ければ実質何もしない）
            pip_path = os.path.join(APP_DIR, "venv", "bin", "pip")
            req_path = os.path.join(APP_DIR, "requirements.txt")
            if os.path.exists(pip_path) and os.path.exists(req_path):
                log("pip install を実行します...")
                pip_result = subprocess.run(
                    [pip_path, "install", "-r", req_path],
                    capture_output=True, text=True, timeout=180,
                )
                if pip_result.returncode != 0:
                    log(f"pip installで警告（続行します）: {pip_result.stderr}")
                else:
                    log("pip install 完了")
            else:
                log(f"pip installをスキップ（pip_path存在={os.path.exists(pip_path)}, "
                    f"req_path存在={os.path.exists(req_path)}）")

            # 新しいコードが最低限壊れていないか確認する
            python_path = os.path.join(APP_DIR, "venv", "bin", "python3")
            main_path = os.path.join(APP_DIR, "main.py")
            log(f"新しいコードの検証を実行します（{python_path}）...")
            check = subprocess.run(
                [python_path, "-m", "py_compile", main_path],
                capture_output=True, text=True, timeout=30,
            )
            if check.returncode != 0:
                raise RuntimeError(f"新しいコードの検証に失敗しました: {check.stderr}")
            log("新しいコードの検証OK")

        except Exception as e:
            msg = f"{e}（元のバージョンに戻しました）"
            log(f"失敗、ロールバックします: {e}\n{traceback.format_exc()}")
            _rollback(backup_dir, moved)
            _write_status("failed", error=msg, target_version=target_version)
            return False, msg

        # ここまでファイルの入れ替え・検証は完了している。
        # この後のsudo呼び出しでサービスが再起動されると、このプロセス自体が
        # 結果を返す前に強制終了する可能性が高いため、"success"は今のうちに
        # ファイルへ書き込んでおく（実際の再起動が失敗した場合の考慮は下記）。
        log("root権限が必要な仕上げ処理（pophug-netctlの入れ替え・サービス再起動）を実行します...")
        _write_status("success", target_version=target_version)

        try:
            r = subprocess.run(
                ["sudo", UPDATE_APPLY_PATH, APP_DIR],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            # ここに到達した＝プロセスは生きているのでサービス再起動はされていない。
            # ロールバックしてfailedに書き戻す。
            msg = f"仕上げ処理の実行に失敗しました: {e}（元のバージョンに戻しました）"
            log(f"失敗: {msg}\n{traceback.format_exc()}")
            _rollback(backup_dir, moved)
            _write_status("failed", error=msg, target_version=target_version)
            return False, msg

        if r.returncode != 0:
            msg = f"仕上げ処理に失敗しました: {r.stderr}（元のバージョンに戻しました）"
            log(f"失敗: {msg}\nstdout={r.stdout}\nstderr={r.stderr}")
            _rollback(backup_dir, moved)
            _write_status("failed", error=msg, target_version=target_version)
            return False, msg

        log(f"仕上げ処理成功: stdout={r.stdout}")
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
    log(f"ロールバック完了: {moved_names}")
