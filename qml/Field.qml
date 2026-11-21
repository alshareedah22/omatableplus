import QtQuick
import QtQuick.Controls
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons

// A label above a single-line input, in the Omarchy theme.
Item {
  id: field
  property string label: ""
  property alias text: input.text
  property alias placeholder: input.placeholderText
  property bool secret: false
  property bool dirty: false
  readonly property real inputHeight: input.height   // to line a button up with the input

  signal accepted()

  readonly property bool inputFocused: input.activeFocus
  readonly property Item inputItem: input
  property real minInputHeight: 0    // match a taller neighbour, e.g. a dropdown
  property real trailingSpace: 0     // room kept free at the right of the input

  // Equal implicit widths so a RowLayout splits the row evenly between fields.
  implicitWidth: 200
  implicitHeight: column.implicitHeight
  opacity: enabled ? 1 : 0.5

  function setText(value) { input.text = value; dirty = false }
  function focusInput() { input.forceActiveFocus() }

  // Qt's own paste, and its check of whether the clipboard holds text (run on every clipboard
  // change), read what another Wayland app offers, in this thread and without a limit. So a field
  // reads nothing until it has focus, and Ctrl+V / Shift+Insert fetch the text through wl-paste
  // with a time and a size limit instead.
  readonly property int pasteMaxBytes: 65536
  readonly property int pasteSeconds: 2

  function pasteText(text) {
    const clean = String(text).replace(/[\r\n]+/g, " ")
    if (input.selectedText !== "") input.remove(input.selectionStart, input.selectionEnd)
    input.insert(input.cursorPosition, clean)
    dirty = true
  }

  Process {
    id: paster
    command: ["sh", "-c", "timeout " + field.pasteSeconds + " wl-paste --no-newline --type text/plain 2>/dev/null | head -c " + field.pasteMaxBytes]
    stdout: StdioCollector { onStreamFinished: if (text !== "") field.pasteText(text) }
  }

  Column {
    id: column
    width: field.width
    spacing: 4

    Text {
      textFormat: Text.PlainText
      text: field.label
      color: Commons.Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }
    TextField {
      id: input
      width: column.width
      height: Math.max(implicitHeight, field.minInputHeight)
      rightPadding: leftPadding + field.trailingSpace
      readOnly: !activeFocus   // an unfocused field reads no clipboard
      echoMode: field.secret ? TextInput.Password : TextInput.Normal
      color: Commons.Color.foreground
      placeholderTextColor: Commons.Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      onTextEdited: field.dirty = true
      onAccepted: field.accepted()
      Keys.onPressed: function(event) {
        if (event.matches(StandardKey.Paste)) { event.accepted = true; if (!paster.running) paster.running = true }
      }
      background: Rectangle {
        color: Qt.lighter(Commons.Color.background, 1.25)
        border.color: input.activeFocus ? Commons.Color.accent : Qt.lighter(Commons.Color.background, 1.6)
        radius: 4
      }
    }
  }
}
