# ============================================
# KitchenCar POP Signage - sd_watchdog.py
#
# systemdのWatchdog機能向けに、「生きています」という生存通知(sd_notify)を
# 送るための薄いラッパー。python-systemd等の外部ライブラリには依存せず、
# systemdが用意するUNIXドメインソケット（環境変数 NOTIFY_SOCKET が示すパス）へ
# 直接書き込むだけの最小実装にしている。
#
# 使い方（main.py側）:
#   1. 起動処理が全て終わったタイミングで notify_ready() を1回呼ぶ
#      （Type=notify のサービスは、これを受け取るまで「起動中」とみなされる）
#   2. メインループの中で、watchdog_interval_seconds() が返す間隔より
#      短い周期で notify_alive() を呼び続ける
#
# systemd側で WatchdogSec=N を設定したサービスの場合、notify_alive() が
# 一定時間（WatchdogSec）呼ばれなくなると、systemdは「アプリがフリーズした」
# と判断してプロセスをSIGKILLし、Restart=の設定に従って再起動する。
# これにより、pygameのメインループが固まった（例外を出さずに応答しなくなった）
# ケースを検知して自動復帰できるようになる。
#
# 【注意】ここで検知できるのはあくまで「このプロセスのメインループが動き続けて
# いるか」であり、カーネルごと完全にハングした場合（ドライバ起因の完全フリーズ等）
# までは保証しない。そこまで保護したい場合は、別途ハードウェアウォッチドッグ
# （bcm2835_wdt + systemdのRuntimeWatchdogSec）の追加を検討すること。
#
# Mac開発環境やsystemd管理外での直接実行時（`python3 main.py`）は、
# 環境変数NOTIFY_SOCKETが存在しないため、各関数は何もせず静かに戻る。
# 呼び出し側で「systemd配下かどうか」を分岐する必要はない。
# ============================================

import os
import socket

_NOTIFY_SOCKET = os.environ.get("NOTIFY_SOCKET")

DEFAULT_NOTIFY_INTERVAL_SECONDS = 5  # WATCHDOG_USECが取得できない環境でのフォールバック値


def _send(message):
    """systemdへ1行のメッセージを送る。通知用ソケットが無い環境（Macでの開発時、
    systemd管理外での直接起動時など）では何もしない。通知の送信失敗自体で
    アプリ本体を止めたくないので、例外は握りつぶしてログも出さない
    （毎フレーム〜数秒おきに呼ばれる想定のため、失敗のたびにログを出すと
    ノイズになる）。"""
    if not _NOTIFY_SOCKET:
        return
    addr = _NOTIFY_SOCKET
    if addr.startswith("@"):
        # Linuxの「abstract namespace」ソケット。先頭の@は慣習的な表記で、
        # 実際のアドレスはヌルバイト始まりで指定する必要がある
        addr = "\0" + addr[1:]
    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.connect(addr)
        sock.sendall(message.encode("utf-8"))
    except OSError:
        pass
    finally:
        if sock is not None:
            sock.close()


def notify_ready():
    """Type=notifyのサービスに対し、起動処理が完了したことを知らせる。
    これを送るまでsystemdは「起動中」とみなし続ける
    （呼ばないと起動タイムアウト扱いになる場合がある）。"""
    _send("READY=1")


def notify_alive():
    """ウォッチドッグへの生存通知。WatchdogSec=Nが設定されたサービスで、
    これをwatchdog_interval_seconds()より短い周期で送り続けないと、
    systemdにフリーズしたと判断されてプロセスがkillされ、
    Restart=の設定に従って再起動される。"""
    _send("WATCHDOG=1")


def notify_stopping():
    """終了処理に入ったことをsystemdに知らせる（任意。呼ばなくても動作に支障はない）。"""
    _send("STOPPING=1")


def watchdog_interval_seconds(default=DEFAULT_NOTIFY_INTERVAL_SECONDS, safety_divisor=2):
    """生存通知を送るべき間隔（秒）を返す。

    systemdはWatchdogSec=を設定したサービスの子プロセスに対し、環境変数
    WATCHDOG_USEC（WatchdogSec=の値をマイクロ秒に換算したもの）を自動的に
    渡してくれる。これを読み取り、余裕を持ってその半分の間隔で通知するよう
    計算する（例: WatchdogSec=30 なら15秒間隔）。

    こうしておくことで、将来systemdユニット側でWatchdogSec=の値を変更しても、
    main.py側の間隔設定を手動で追従させる必要がなくなる
    （2箇所に同じ値を書いて食い違う、という事故を防げる）。

    環境変数が取得できない場合（Mac開発時、WatchdogSec未設定時など）は
    defaultを返す。"""
    raw = os.environ.get("WATCHDOG_USEC")
    if not raw:
        return default
    try:
        usec = int(raw)
    except ValueError:
        return default
    if usec <= 0:
        return default
    return (usec / 1_000_000) / safety_divisor
