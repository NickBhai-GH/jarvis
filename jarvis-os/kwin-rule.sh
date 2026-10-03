#!/bin/sh
# Adds (or with "remove", deletes) the KWin window rule that makes JARVIS OS behave like a wallpaper:
# below every window, no border, out of taskbar/alt-tab/pager, never takes keyboard focus, pinned to one 1920x1080 monitor.
#   ./kwin-rule.sh [X,Y]     X,Y = that monitor's top-left corner in the desktop layout (default 0,0)
#   ./kwin-rule.sh remove
# Your other KWin rules are kept: only the [jarvis-os] group and its name in the General rules list change.
set -e
cfg="${XDG_CONFIG_HOME:-$HOME/.config}/kwinrulesrc"
rules=$(kreadconfig6 --file kwinrulesrc --group General --key rules | tr ',' '\n' | grep -vx 'jarvis-os' | paste -sd, -)
if [ "$1" = remove ]; then
  [ -f "$cfg" ] && awk '/^\[/{skip = ($0 == "[jarvis-os]")} !skip' "$cfg" > "$cfg.tmp" && mv "$cfg.tmp" "$cfg"
else
  K="kwriteconfig6 --file kwinrulesrc --group jarvis-os --key"
  $K Description "JARVIS OS dashboard"
  $K wmclass jarvis-os;     $K wmclassmatch 1
  $K below true;            $K belowrule 2
  $K above false;           $K aboverule 2
  $K noborder true;         $K noborderrule 2
  $K skiptaskbar true;      $K skiptaskbarrule 2
  $K skipswitcher true;     $K skipswitcherrule 2
  $K skippager true;        $K skippagerrule 2
  $K acceptfocus false;     $K acceptfocusrule 2
  $K position "${1:-0,0}";  $K positionrule 2
  $K size 1920,1080;        $K sizerule 2
  rules=${rules:+$rules,}jarvis-os
fi
kwriteconfig6 --file kwinrulesrc --group General --key rules "$rules"
kwriteconfig6 --file kwinrulesrc --group General --key count "$(printf '%s' "$rules" | tr ',' '\n' | grep -c .)"
qdbus org.kde.KWin /KWin reconfigure
