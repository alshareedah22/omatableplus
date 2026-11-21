import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

// A command console under a browser: a log of commands and replies and an
// input with history (Up/Down, or Ctrl+Up/Down when multi-line).
Rectangle {
  id: con

  property var helper: null
  property string sid: ""
  property string connId: ""
  property string method: ""          // redis.cmd or mongo.run
  property bool multiline: false
  property string placeholder: ""
  property var formatter: function(r) { return JSON.stringify(r) }
  property var log: []                // [{ cmd, out, error, ms }]
  property var past: []               // newest first
  property int pastIndex: -1
  property bool busy: false
  signal ran(var result)
  signal closeRequested()

  function focusInput() { (multiline ? area : line).forceActiveFocus() }
  function inputText() { return multiline ? area.text : line.text }
  function setInput(t) { if (multiline) area.text = t; else line.text = t }

  function loadPast() {
    helper.request("query.history", { conn: connId, limit: 100 }, function(r, e) {
      if (!e) past = r.items.map(function(i) { return i.sql })
    })
  }

  function run() {
    const text = inputText().trim()
    if (!text || busy) return
    busy = true
    past = [text].concat(past.filter(function(p) { return p !== text }))
    pastIndex = -1
    helper.request(method, { session: sid, text: text }, function(r, e) {
      busy = false
      const entry = e ? { cmd: text, out: e.message, error: true, ms: 0 } : { cmd: text, out: con.formatter(r), error: false, ms: r.ms }
      log = log.concat([entry]).slice(-200)
      Qt.callLater(function() { logView.positionViewAtEnd() })
      if (!e) { setInput(""); con.ran(r) }
    })
  }

  function step(dir) {
    if (!past.length) return
    pastIndex = Math.max(-1, Math.min(past.length - 1, pastIndex + dir))
    setInput(pastIndex < 0 ? "" : past[pastIndex])
  }

  onVisibleChanged: if (visible) { loadPast(); focusInput() }

  color: Qt.lighter(Commons.Color.background, 1.04)
  Rectangle { width: parent.width; height: 1; color: Qt.lighter(Commons.Color.background, 1.5) }

  ColumnLayout {
    anchors.fill: parent
    anchors.margins: 8
    spacing: 6
    RowLayout {
      Layout.fillWidth: true
      Text { textFormat: Text.PlainText; text: "Console"; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; font.bold: true }
      Item { Layout.fillWidth: true }
      Ui.Button { text: "Clear"; fontSize: Style.font.bodySmall; onClicked: con.log = [] }
      Ui.Button { text: "✕"; fontSize: Style.font.bodySmall; tooltipText: "Hide the console"; onClicked: con.closeRequested() }
    }
    ListView {
      id: logView
      Layout.fillWidth: true
      Layout.fillHeight: true
      clip: true
      spacing: 6
      model: con.log
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
      delegate: Column {
        required property var modelData
        width: logView.width - 12
        spacing: 2
        TextEdit {
          width: parent.width
          readOnly: true
          selectByMouse: true
          wrapMode: TextEdit.Wrap
          text: "› " + modelData.cmd + (modelData.ms ? "    " + modelData.ms + " ms" : "")
          color: Commons.Color.accent
          font.family: "monospace"
          font.pixelSize: Style.font.bodySmall
        }
        TextEdit {
          width: parent.width
          readOnly: true
          selectByMouse: true
          wrapMode: TextEdit.Wrap
          text: modelData.out
          color: modelData.error ? Commons.Color.urgent : Commons.Color.foreground
          font.family: "monospace"
          font.pixelSize: Style.font.bodySmall
        }
      }
      Text {
        textFormat: Text.PlainText
        visible: con.log.length === 0
        text: con.multiline ? "Run a database command, e.g. {ping: 1} or {dbStats: 1}. Ctrl+Enter runs it." : "Type a command, e.g. INFO memory or HGETALL key, and press Enter."
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
    }
    RowLayout {
      Layout.fillWidth: true
      spacing: 6
      Ui.TextField {
        id: line
        readOnly: !activeFocus   // an unfocused field reads no clipboard
        visible: !con.multiline
        Layout.fillWidth: true
        placeholderText: con.placeholder
        font.family: "monospace"
        Keys.onReturnPressed: con.run()
        Keys.onEnterPressed: con.run()
        Keys.onUpPressed: con.step(1)
        Keys.onDownPressed: con.step(-1)
      }
      ScrollView {
        visible: con.multiline
        Layout.fillWidth: true
        Layout.preferredHeight: Math.round(Style.font.body * 4.5)
        TextArea {
          id: area
          readOnly: !activeFocus   // an unfocused field reads no clipboard
          placeholderText: con.placeholder
          color: Commons.Color.foreground
          placeholderTextColor: Commons.Color.muted
          font.family: "monospace"
          font.pixelSize: Style.font.body
          wrapMode: TextEdit.Wrap
          background: Rectangle { color: Qt.lighter(Commons.Color.background, 1.2); border.color: area.activeFocus ? Commons.Color.accent : Qt.lighter(Commons.Color.background, 1.6); radius: 4 }
          Keys.onPressed: function(event) {
            const ctrl = event.modifiers & Qt.ControlModifier
            if (ctrl && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) { con.run(); event.accepted = true }
            else if (ctrl && event.key === Qt.Key_Up) { con.step(1); event.accepted = true }
            else if (ctrl && event.key === Qt.Key_Down) { con.step(-1); event.accepted = true }
          }
        }
      }
      Ui.Button { bordered: true; text: con.busy ? "Running…" : "Run"; enabled: !con.busy; onClicked: con.run() }
    }
  }
}
