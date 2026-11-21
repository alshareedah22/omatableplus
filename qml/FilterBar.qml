import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

// Column · operator · value rows joined by AND/OR. Emits `apply` with the
// filter list the helper understands; empty values are skipped.
Rectangle {
  id: bar

  property var columns: []          // column names
  property string match: "and"
  signal apply(var filters, string match)

  readonly property var ops: [
    { value: "=", label: "=" }, { value: "<>", label: "≠" }, { value: "<", label: "<" },
    { value: ">", label: ">" }, { value: "<=", label: "≤" }, { value: ">=", label: "≥" },
    { value: "contains", label: "contains" }, { value: "not_contains", label: "not contains" },
    { value: "starts", label: "starts with" }, { value: "ends", label: "ends with" },
    { value: "in", label: "in (a, b, c)" }, { value: "null", label: "is NULL" },
    { value: "not_null", label: "is not NULL" }]

  implicitHeight: body.implicitHeight + 12
  color: Qt.lighter(Commons.Color.background, 1.08)

  function addFilter() {
    filterModel.append({ column: columns.length ? columns[0] : "", op: "contains", value: "" })
    focusTimer.restart()
  }

  function collect() {
    const out = []
    for (let i = 0; i < filterModel.count; i++) {
      const f = filterModel.get(i)
      const noValue = f.op === "null" || f.op === "not_null"
      if (!f.column || (!noValue && f.value === "")) continue
      out.push(noValue ? { column: f.column, op: f.op } : { column: f.column, op: f.op, value: f.value })
    }
    return out
  }

  function run() { apply(collect(), match) }

  function clear() {
    filterModel.clear()
    run()
  }

  ListModel { id: filterModel }
  Timer { id: focusTimer; interval: 50; onTriggered: { const it = rows.itemAt(rows.count - 1); if (it) it.focusValue() } }

  ColumnLayout {
    id: body
    x: 8; y: 6
    width: bar.width - 16
    spacing: 6

    Repeater {
      id: rows
      model: filterModel
      delegate: RowLayout {
        id: frow
        required property int index
        required property string column
        required property string op
        required property string value
        spacing: 6
        Layout.fillWidth: true
        function focusValue() { valueField.forceActiveFocus() }

        Text {
          textFormat: Text.PlainText
          Layout.preferredWidth: 36
          text: frow.index === 0 ? "where" : bar.match
          color: Commons.Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
        }
        Ui.Dropdown {
          Layout.preferredWidth: 180
          showLabel: false
          options: bar.columns
          value: frow.column
          onChanged: function(v) { filterModel.setProperty(frow.index, "column", v) }
        }
        Ui.Dropdown {
          Layout.preferredWidth: 140
          showLabel: false
          options: bar.ops
          value: frow.op
          onChanged: function(v) { filterModel.setProperty(frow.index, "op", v) }
        }
        Ui.TextField {
          id: valueField
          readOnly: !activeFocus   // an unfocused field reads no clipboard
          Layout.fillWidth: true
          visible: frow.op !== "null" && frow.op !== "not_null"
          text: frow.value
          placeholderText: "value"
          font.family: Style.font.family
          onTextEdited: filterModel.setProperty(frow.index, "value", text)
          onAccepted: bar.run()
        }
        Item { Layout.fillWidth: true; visible: !valueField.visible }
        Ui.Button { text: "✕"; tooltipText: "Remove filter"; onClicked: { filterModel.remove(frow.index); bar.run() } }
      }
    }

    RowLayout {
      spacing: 6
      Ui.Button { bordered: true; text: "+ Filter"; tooltipText: "Add a filter (Ctrl+F)"; onClicked: bar.addFilter() }
      Ui.Button {
        visible: filterModel.count > 1
        bordered: true
        text: bar.match === "and" ? "Match all" : "Match any"
        onClicked: { bar.match = bar.match === "and" ? "or" : "and"; bar.run() }
      }
      Ui.Button { visible: filterModel.count > 0; bordered: true; text: "Apply"; onClicked: bar.run() }
      Ui.Button { visible: filterModel.count > 0; text: "Clear"; onClicked: bar.clear() }
    }
  }
}
