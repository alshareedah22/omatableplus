#!/bin/bash

# app/omatableplus setup, runtime folder and uninstall, in a throwaway HOME (no app is started).
#
#   tests/test-launcher.sh

set -uo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
L="$here/app/omatableplus"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
export HOME="$T/home" XDG_STATE_HOME="$T/state" XDG_DATA_HOME="$T/data" XDG_RUNTIME_DIR="$T/run" OMATABLEPLUS_SKIP_DRIVERS=1
mkdir -p "$HOME/.config/hypr" "$T/bin" "$T/run"
printf -- '-- mine\n' >"$HOME/.config/hypr/bindings.lua"
printf '#!/bin/bash\nexit 0\n' >"$T/bin/hyprctl"; chmod +x "$T/bin/hyprctl"
export PATH="$T/bin:$PATH"

passed=0 failed=0
ok() { passed=$((passed + 1)); printf '  \e[32m✓\e[0m %s\n' "$1"; }
bad() { failed=$((failed + 1)); printf '  \e[31m✗ %s\e[0m\n' "$1"; }
eq() { if [[ $2 == "$3" ]]; then ok "$1"; else bad "$1: got [$2], want [$3]"; fi; }
has() { if [[ $2 == *"$3"* ]]; then ok "$1"; else bad "$1: [$2] lacks [$3]"; fi; }

echo "Setup"
out=$("$L" setup)
has "says it is in the launcher" "$out" "app launcher"
D="$XDG_DATA_HOME/applications/omatableplus.desktop"
has "writes a launcher entry" "$(cat "$D")" "Exec=$here/app/omatableplus"
has "with the app's window class" "$(cat "$D")" "StartupWMClass=omatableplus"
has "and a New window action" "$(cat "$D")" "Exec=$here/app/omatableplus new-window"
has "binds SUPER+ALT+T" "$(cat "$HOME/.config/hypr/bindings.lua")" "o.bind(\"SUPER + ALT + T\", \"OmaTablePlus\""
R="$XDG_STATE_HOME/omatableplus/app"
eq "links the app's root file" "$(readlink "$R/shell.qml")" "$here/app/shell.qml"
eq "links Omarchy's Commons" "$(readlink "$R/Commons")" "${OMARCHY_PATH:-/usr/share/omarchy}/shell/Commons"
for n in qml tablectl icon.svg Ui; do [[ -L $R/$n ]] && ok "links $n" || bad "links $n"; done
eq "runs once" "$("$L" setup)" "already set up"

echo "Taken key"
rm -rf "$XDG_STATE_HOME" ; printf 'o.bind("SUPER + ALT + T", "Mine", "x")\n' >"$HOME/.config/hypr/bindings.lua"
has "leaves a taken SUPER+ALT+T alone" "$("$L" setup)" "is taken"
eq "and the file unchanged" "$(cat "$HOME/.config/hypr/bindings.lua")" 'o.bind("SUPER + ALT + T", "Mine", "x")'

echo "Uninstall"
printf -- '-- mine\n' >"$HOME/.config/hypr/bindings.lua"; rm -rf "$XDG_STATE_HOME"; "$L" setup >/dev/null
"$L" uninstall >/dev/null
[[ ! -e $D ]] && ok "removes the launcher entry" || bad "removes the launcher entry"
eq "removes the shortcut" "$(cat "$HOME/.config/hypr/bindings.lua")" "-- mine"
[[ ! -e $R ]] && ok "removes the runtime folder" || bad "removes the runtime folder"
has "a reinstall sets up again" "$("$L" setup)" "app launcher"

echo "Managed (symlinked) and private bindings"
rm -rf "$XDG_STATE_HOME"; mkdir -p "$T/dotfiles"
printf -- '-- managed\n' >"$T/dotfiles/bindings.lua"; rm -f "$HOME/.config/hypr/bindings.lua"
ln -s "$T/dotfiles/bindings.lua" "$HOME/.config/hypr/bindings.lua"
has "says a symlinked file is left alone" "$("$L" setup)" "symlink"
[[ -L $HOME/.config/hypr/bindings.lua ]] && ok "keeps the symlink" || bad "keeps the symlink"
eq "does not touch the managed file" "$(cat "$T/dotfiles/bindings.lua")" "-- managed"
printf -- '-- managed\n%s\n%s\n' "-- OmaTablePlus (omatableplus uninstall removes these two lines)" "o.bind(\"SUPER + ALT + T\", \"OmaTablePlus\", \"$here/app/omatableplus\")" >"$T/dotfiles/bindings.lua"
before=$(cat "$T/dotfiles/bindings.lua")
"$L" uninstall >/dev/null
[[ -L $HOME/.config/hypr/bindings.lua ]] && ok "uninstall keeps the symlink" || bad "uninstall keeps the symlink"
eq "uninstall leaves the managed file" "$(cat "$T/dotfiles/bindings.lua")" "$before"
rm -f "$HOME/.config/hypr/bindings.lua" ; rm -rf "$XDG_STATE_HOME"
printf -- '-- mine\n' >"$HOME/.config/hypr/bindings.lua"; chmod 600 "$HOME/.config/hypr/bindings.lua"
"$L" setup >/dev/null
eq "setup keeps the file's mode" "$(stat -c %a "$HOME/.config/hypr/bindings.lua")" "600"
"$L" uninstall >/dev/null
eq "uninstall keeps the file's mode" "$(stat -c %a "$HOME/.config/hypr/bindings.lua")" "600"

echo "Moved plugin or Omarchy"
rm -rf "$XDG_STATE_HOME"; "$L" setup >/dev/null
echo "old|place" >"$R/.source"
"$L" help >/dev/null
OMARCHY_PATH=/usr/share/omarchy bash -c "source <(sed -n '/^runtime()/,/^}/p' '$L'); ROOT='$here'; OMARCHY_SHELL=/usr/share/omarchy/shell; RUNTIME='$R'; fail(){ exit 1; }; runtime"
eq "relinks when the source changed" "$(cat "$R/.source")" "$here|/usr/share/omarchy/shell"

echo
echo "$passed passed, $failed failed"
((failed == 0))
