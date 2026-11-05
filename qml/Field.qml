import QtQuick
import QtQuick.Controls
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
      echoMode: field.secret ? TextInput.Password : TextInput.Normal
      color: Commons.Color.foreground
      placeholderTextColor: Commons.Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      onTextEdited: field.dirty = true
      background: Rectangle {
        color: Qt.lighter(Commons.Color.background, 1.25)
        border.color: input.activeFocus ? Commons.Color.accent : Qt.lighter(Commons.Color.background, 1.6)
        radius: 4
      }
    }
  }
}
