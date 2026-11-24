# 可丟棄的 RDP 測試靶，給 FreeRDP 主控台引擎做功能回歸用。
#
# 為什麼要有它：拿真實機器當靶有兩個問題 —— 動到別人正在用的機器，
# 而且 GNOME 遠端登入會累積收不掉的登入畫面工作階段，跑個幾十次之後
# 就只剩黑畫面（見 docs/troubleshooting.html 的「遠端主控台」）。
#
# 選 xrdp 是刻意的：它正是內建的 aardwolf 引擎連不上（NTLM 不送 MIC）、
# 而 FreeRDP 引擎存在理由的那種目標。
#
# 會話內容刻意極簡 —— 一個大字級的 xterm。打什麼畫面上就看得到什麼，
# 所以「鍵盤有沒有送到」「貼上有沒有進去」「滾輪有沒有捲」全部肉眼可驗。
#
#   docker build -t jtipam-rdp-target scripts/rdp-test-target
#   docker run -d --name jtipam-rdp-target -p 127.0.0.1:13389:3389 jtipam-rdp-target
#   # 帳號 rdpuser / 密碼 TestPw-123
#   docker rm -f -v jtipam-rdp-target
#
# ⚠️ 只綁 127.0.0.1。這是一台密碼寫在原始碼裡的 RDP 伺服器，不可以對外開。
FROM debian:12

RUN apt-get update && apt-get install -y --no-install-recommends \
        xrdp tigervnc-standalone-server xterm openbox x11-utils dbus-x11 xfonts-base \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -s /bin/bash rdpuser && echo 'rdpuser:TestPw-123' | chpasswd

# xrdp 預設走 Xorg 後端，而這裡沒有裝 Xorg（裝的是 Xvnc）。不拿掉 [Xorg] 區段的話
# sesman 會去執行不存在的 /usr/lib/xorg/Xorg，然後連上去只看得到一片黑。
RUN awk '/^\[Xorg\]/{skip=1} /^\[/{ if ($0 != "[Xorg]") skip=0 } !skip' \
        /etc/xrdp/xrdp.ini > /tmp/x.ini && mv /tmp/x.ini /etc/xrdp/xrdp.ini

# Shift+Insert 改成貼 CLIPBOARD（xterm 預設貼的是 PRIMARY），
# 這樣「控制端貼上」才驗得到 —— 我們寫進去的是 CLIPBOARD。
RUN printf '%s\n' '#!/bin/sh' 'openbox &' \
    "exec xterm -geometry 100x28+20+20 -fa Monospace -fs 16 -bg black -fg white \\" \
    "  -xrm 'XTerm*VT100.translations: #override Shift <Key>Insert: insert-selection(CLIPBOARD)'" \
    > /etc/xrdp/startwm.sh && chmod +x /etc/xrdp/startwm.sh

# 註：沒有裝中文字型，所以貼中文進去會顯示成 #。那是這個靶的字型問題，不是剪貼簿壞掉。
RUN mkdir -p /var/run/xrdp && xrdp-keygen xrdp auto 2048 >/dev/null 2>&1 || true

EXPOSE 3389
CMD ["/bin/sh","-c","mkdir -p /var/run/xrdp && /usr/sbin/xrdp-sesman --nodaemon & sleep 1; exec /usr/sbin/xrdp --nodaemon"]
