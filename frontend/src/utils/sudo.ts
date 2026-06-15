/**
 * Shell 片段：只有「非 root」時才加 sudo。
 *
 * 很多派送目標（Proxmox VE / PBS / PDM、精簡 appliance / 容器）本來就是 root 且**沒有** sudo，
 * 寫死 `sudo …` 會出 `sudo: command not found`。用這段在 root 時展開成空字串、非 root 時展開成 `sudo`。
 *
 * 使用注意：
 * - 後面接「真正的指令字」(如 `bash …` / `apt …` / `env …`) 時直接用：`${SUDO} bash …`。
 * - 若要**帶環境變數**(如 `VAR=val … bash`)，一定要透過 `env`：`${SUDO} env VAR=val bash`。
 *   因為 root 時 `${SUDO}` 展開成空，`VAR=val` 會被當成「指令」而非賦值（$(...) 才是指令字）。
 */
export const SUDO = '$([ "$(id -u)" -ne 0 ] && echo sudo)';
