import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Commons as Commons

// A colour swatch that opens a picker: preset colours, a saturation/brightness
// square and a hue bar. `value` is "#rrggbb"; `picked` fires on every change.
Item {
  id: picker
  property string value: ""
  property string fallback: "#888888"      // shown when value is empty or not a colour
  property var presets: []
  // the popup hangs below this item, and pressing inside it does not close the popup
  property Item anchorItem: picker
  readonly property bool opened: popup.visible
  signal picked(string hex)

  readonly property bool valid: /^#[0-9a-fA-F]{6}$/.test(value)
  readonly property color shown: valid ? value : fallback

  // working colour while the popup is open
  property real hue: 0
  property real sat: 0
  property real val: 0

  // a colour typed into the hex field moves the square and the hue bar too
  onValueChanged: if (valid && toHex(Qt.hsva(hue, sat, val, 1)) !== value.toLowerCase()) take(shown)

  implicitWidth: 32
  implicitHeight: 32
  opacity: enabled ? 1 : 0.5

  function hex2(n) { const s = Math.round(n * 255).toString(16); return s.length < 2 ? "0" + s : s }
  function toHex(c) { return "#" + hex2(c.r) + hex2(c.g) + hex2(c.b) }
  function emit() { picker.picked(toHex(Qt.hsva(hue, sat, val, 1))) }
  function open() { if (!popup.visible) { take(shown); popup.open() } }
  function close() { popup.close() }
  function take(c) {
    // greys have no hue: keep the last one so the square does not jump
    if (c.hsvSaturation > 0 && c.hsvHue >= 0) hue = c.hsvHue
    sat = c.hsvSaturation
    val = c.hsvValue
  }

  Rectangle {
    id: swatch
    anchors.fill: parent
    radius: 4
    color: picker.shown
    border.color: popup.visible || area.containsMouse ? Commons.Color.accent : Qt.lighter(Commons.Color.background, 1.6)
    border.width: 1
    MouseArea {
      id: area
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: picker.opened ? picker.close() : picker.open()
    }
  }

  Popup {
    id: popup
    parent: picker.anchorItem
    y: picker.anchorItem.height + 4
    x: picker.anchorItem.width - width
    padding: 10
    focus: false     // keep typing in the hex field while the popup is open
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
    background: Rectangle { color: Commons.Color.menu.background; border.color: Commons.Color.menu.border; border.width: 1; radius: 4 }

    Column {
      spacing: 10

      Grid {
        id: presetGrid
        columns: 8
        spacing: 6
        Repeater {
          model: picker.presets
          delegate: Rectangle {
            required property string modelData
            width: 22; height: 22; radius: 4
            color: modelData
            border.width: modelData.toLowerCase() === picker.value.toLowerCase() || presetArea.containsMouse ? 2 : 1
            border.color: modelData.toLowerCase() === picker.value.toLowerCase() ? Commons.Color.foreground
              : presetArea.containsMouse ? Commons.Color.accent : Qt.darker(modelData, 1.4)
            MouseArea {
              id: presetArea
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: { picker.take(parent.color); picker.picked(parent.modelData.toLowerCase()) }
            }
          }
        }
      }

      Row {
        spacing: 8

        // saturation left to right, brightness bottom to top
        Item {
          id: square
          width: presetGrid.width - 8 - hueBar.width
          height: 140
          Rectangle {
            anchors.fill: parent
            radius: 4
            gradient: Gradient {
              orientation: Gradient.Horizontal
              GradientStop { position: 0; color: "#ffffff" }
              GradientStop { position: 1; color: Qt.hsva(picker.hue, 1, 1, 1) }
            }
          }
          Rectangle {
            anchors.fill: parent
            radius: 4
            gradient: Gradient {
              GradientStop { position: 0; color: "transparent" }
              GradientStop { position: 1; color: "#000000" }
            }
          }
          Rectangle {
            width: 12; height: 12; radius: 6
            x: picker.sat * square.width - 6
            y: (1 - picker.val) * square.height - 6
            color: "transparent"
            border.color: picker.val > 0.5 ? "#000000" : "#ffffff"
            border.width: 2
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.CrossCursor
            function pick(m) {
              picker.sat = Math.max(0, Math.min(1, m.x / width))
              picker.val = Math.max(0, Math.min(1, 1 - m.y / height))
              picker.emit()
            }
            onPressed: function(m) { pick(m) }
            onPositionChanged: function(m) { if (pressed) pick(m) }
          }
        }

        Item {
          id: hueBar
          width: 16
          height: square.height
          Rectangle {
            anchors.fill: parent
            radius: 4
            gradient: Gradient {
              GradientStop { position: 0 / 6; color: "#ff0000" }
              GradientStop { position: 1 / 6; color: "#ffff00" }
              GradientStop { position: 2 / 6; color: "#00ff00" }
              GradientStop { position: 3 / 6; color: "#00ffff" }
              GradientStop { position: 4 / 6; color: "#0000ff" }
              GradientStop { position: 5 / 6; color: "#ff00ff" }
              GradientStop { position: 6 / 6; color: "#ff0000" }
            }
          }
          Rectangle {
            x: -2; width: hueBar.width + 4; height: 4; radius: 2
            y: picker.hue * hueBar.height - 2
            color: "transparent"
            border.color: Commons.Color.foreground
            border.width: 2
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            function pick(m) {
              picker.hue = Math.max(0, Math.min(0.999, m.y / height))
              if (picker.sat === 0) picker.sat = 1
              if (picker.val === 0) picker.val = 1
              picker.emit()
            }
            onPressed: function(m) { pick(m) }
            onPositionChanged: function(m) { if (pressed) pick(m) }
          }
        }
      }

      Row {
        spacing: 8
        Rectangle {
          width: 22; height: 22; radius: 4
          color: picker.shown
          border.color: Qt.lighter(Commons.Color.background, 1.6)
        }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: picker.valid ? picker.value.toLowerCase() : "Engine default"
          color: Commons.Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }
      }
    }
  }
}
