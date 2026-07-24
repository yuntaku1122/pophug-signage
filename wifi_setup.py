# ============================================
# KitchenCar POP Signage - wifi_setup.py
#
# 「Wi-Fiセットアップモード」関連のロジックをまとめたモジュール。
# main.py（ボタン長押し検知・画面表示）とupload_server.py（設定ページ）
# の両方から使う。実際にネットワーク設定を変更する処理はroot権限が要るため、
# /usr/local/bin/pophug-netctl をsudo経由で呼び出す形にしている
# （このファイル自身はroot権限を持たない）。
# ============================================

import re
import subprocess

NETCTL_PATH = "/usr/local/bin/pophug-netctl"
HOTSPOT_CON_NAME = "pophug-setup-ap"


def get_wlan_mac(iface="wlan0"):
    """wlan0のMACアドレスを取得する（存在しなければNone）"""
    try:
        with open(f"/sys/class/net/{iface}/address", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def default_setup_ssid(prefix="pophug-setup"):
    """MACアドレス末尾4桁を使って、機体ごとに一意なSSIDを作る。
    同じ機種を複数台並べても電波が衝突しないようにするため。"""
    mac = get_wlan_mac()
    suffix = mac.replace(":", "")[-4:].upper() if mac else "0000"
    return f"{prefix}-{suffix}"


def _run_netctl(args, timeout=30):
    """pophug-netctlをsudo経由で実行する（root権限が要る操作用）"""
    try:
        r = subprocess.run(
            ["sudo", NETCTL_PATH] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "timeout"
    except FileNotFoundError:
        return False, "", f"{NETCTL_PATH} が見つかりません。READMEのインストール手順を確認してください"


def start_hotspot(ssid, password):
    return _run_netctl(["hotspot-start", ssid, password])


def stop_hotspot():
    return _run_netctl(["hotspot-stop"])


def connect(ssid, password, timeout=45):
    return _run_netctl(["connect", ssid, password], timeout=timeout)


def scan_networks():
    """周辺のWi-Fi一覧を返す（電波の強い順、SSID重複は除去）"""
    ok, out, _err = _run_netctl(["scan"], timeout=20)
    if not ok:
        return []

    networks = []
    for line in out.splitlines():
        # nmcli -t の出力はSSID:SIGNAL:SECURITY の形式（コロン区切り）
        parts = line.split(":")
        if len(parts) >= 3 and parts[0]:
            try:
                signal = int(parts[1])
            except ValueError:
                signal = 0
            networks.append({"ssid": parts[0], "signal": signal, "security": parts[2]})

    seen = set()
    unique = []
    for n in sorted(networks, key=lambda x: -x["signal"]):
        if n["ssid"] not in seen:
            seen.add(n["ssid"])
            unique.append(n)
    return unique


def is_hotspot_active():
    """セットアップ用アクセスポイントが現在有効か（read-onlyなのでsudo不要）"""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=10,
        )
        return HOTSPOT_CON_NAME in r.stdout.splitlines()
    except Exception:
        return False


def is_client_connected():
    """wlan0が（自分専用のホットスポットではなく）通常のWi-Fiクライアントとして
    どこかのネットワークに接続できているかを確認する（read-onlyなのでsudo不要）。
    スタンドアロンモードへの自動移行判定に使う。"""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "802-11-wireless" and parts[0] != HOTSPOT_CON_NAME:
                return True
        return False
    except Exception:
        return False


def current_connection_info():
    """現在クライアントとして接続しているWi-FiのSSIDとIPアドレスを返す
    （read-onlyなのでsudo不要）。繋がっていなければNoneを返す。"""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] == "802-11-wireless" and parts[0] != HOTSPOT_CON_NAME:
                ssid, device = parts[0], parts[2]
                ip = ""
                ip_r = subprocess.run(
                    ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", device],
                    capture_output=True, text=True, timeout=10,
                )
                for ip_line in ip_r.stdout.splitlines():
                    if ip_line.startswith("IP4.ADDRESS"):
                        ip = ip_line.split(":", 1)[1].split("/")[0]
                        break
                return {"ssid": ssid, "ip": ip}
        return None
    except Exception:
        return None


def current_network_mode():
    """現在の接続状態を 'standalone'（自分専用APで待機中） / 'client'（外部Wi-Fiに接続中） /
    'none'（どちらでもない）のいずれかで返す。Webページでの状態表示に使う。"""
    if is_hotspot_active():
        return "standalone"
    if is_client_connected():
        return "client"
    return "none"


def get_hotspot_ip(default="10.42.0.1"):
    """アクセスポイントの実際のIPアドレスを取得する。
    NetworkManagerのshared接続は通常10.42.0.1になるが、
    念のため実際の値を読みに行き、取得できなければ既定値にフォールバックする。"""
    try:
        r = subprocess.run(
            ["nmcli", "-g", "IP4.ADDRESS", "connection", "show", HOTSPOT_CON_NAME],
            capture_output=True, text=True, timeout=10,
        )
        addr = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        return addr.split("/")[0] if addr else default
    except Exception:
        return default


def _escape_wifi_qr(value):
    """WIFI:形式QRコードの特殊文字（; , : \\ "）をエスケープする"""
    return re.sub(r'([\\;,":])', r'\\\1', value)


def wifi_qr_payload(ssid, password):
    """iOS/Androidのカメラで直接読み取ってWi-Fi接続できる標準フォーマットの文字列を作る"""
    security = "WPA" if password else "nopass"
    return f"WIFI:T:{security};S:{_escape_wifi_qr(ssid)};P:{_escape_wifi_qr(password)};;"
