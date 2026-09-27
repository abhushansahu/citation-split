#!/bin/zsh
# Run the split as a background service (launchd user agent) so "Citation Split"
# in the sound menu just works. Usage: ./service.sh install | uninstall | status | log | restart
cd "$(dirname "$0")"; DIR=$(pwd); LABEL=com.abhushan.citation-split; PLIST=~/Library/LaunchAgents/$LABEL.plist; LOG=$DIR/service.log
case "$1" in
  install)
    [ -x app/CitationSplit.app/Contents/MacOS/CitationSplit ] || { echo "app wrapper missing: run ./build-app.sh"; exit 1; }
    mkdir -p ~/Library/LaunchAgents
    cat > "$PLIST" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$DIR/app/CitationSplit.app/Contents/MacOS/CitationSplit</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
P
    launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null; launchctl bootstrap gui/$(id -u) "$PLIST" && echo "installed and started. Log: ./service.sh log"
    echo "macOS will ask to allow \"Citation Split\" to use the microphone: click Allow (it needs the virtual input and the sync chirps)." ;;
  uninstall) launchctl bootout gui/$(id -u) "$PLIST" 2>/dev/null; rm -f "$PLIST"; echo "removed" ;;
  restart) launchctl kickstart -k gui/$(id -u)/$LABEL && echo restarted ;;
  status) launchctl print gui/$(id -u)/$LABEL 2>/dev/null | grep -E "state|pid" | head -3 || echo "not installed"; tail -3 "$LOG" 2>/dev/null ;;
  log) tail -f "$LOG" ;;
  *) echo "usage: ./service.sh install|uninstall|status|log|restart" ;;
esac
