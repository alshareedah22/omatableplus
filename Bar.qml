import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import qs.Ui
import qs.Commons
import qs.Commons as Commons

// Bar icon: opens OmaTablePlus, which runs as its own app (app/omatableplus), or brings it forward.
// A dot under the icon shows while the app runs.
BarWidget {
  id: root
  moduleName: "blackcode.omatableplus"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property string launcher: decodeURIComponent(String(Qt.resolvedUrl("app/omatableplus")).replace(/^file:\/\//, ""))
  // the app's windows, from Hyprland's window list: no polling
  readonly property var appWindows: Hyprland.toplevels.values.filter(function(t) {
    return t.lastIpcObject && t.lastIpcObject.class === "omatableplus"
  })
  readonly property bool running: appWindows.length > 0

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // The glyph only reserves the slot; the color icon below is what shows.
    text: ""
    labelVisible: false
    fixedWidth: vertical ? -1 : icon.width + Style.spaceReal(horizontalMargin) * 2
    tooltipText: (root.running ? "OmaTablePlus · " + root.appWindows.length + (root.appWindows.length === 1 ? " window" : " windows") : "OmaTablePlus")
      + "\nRight-click: new window"
    // the launcher opens the app, or brings it forward when it already runs
    onPressed: function(b) { Quickshell.execDetached(b === Qt.RightButton ? [root.launcher, "new-window"] : [root.launcher]) }

    Image {
      id: icon
      anchors.centerIn: parent
      source: Qt.resolvedUrl("icon.svg")
      width: Math.round(Style.font.body * 1.35)
      height: width
      sourceSize: Qt.size(width * 2, height * 2)
      smooth: true
      // a dot under the icon while the app runs
      Rectangle {
        visible: root.running
        width: Math.max(4, Math.round(Style.font.caption * 0.35))
        height: width
        radius: width / 2
        x: (parent.width - width) / 2
        y: parent.height + 1
        color: Commons.Color.accent
      }
    }
  }

  // First run: launcher entry, SUPER+ALT+T and the database drivers. `setup` is a no-op once it
  // has run (done-marker and lock inside the launcher), so every copy of this widget may start it.
  Process {
    id: setup
    command: [root.launcher, "setup"]
    stderr: StdioCollector {
      onStreamFinished: if (text.trim() !== "") console.warn("OmaTablePlus setup: " + text.trim())
    }
  }
  Component.onCompleted: setup.running = true
}
