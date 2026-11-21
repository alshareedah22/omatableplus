import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

// Schema tree: schemas → tables, views, functions, sequences, with search.
Rectangle {
  id: side

  property var tree: []
  property string filter: ""
  property string selected: ""     // "schema.table"
  property var collapsed: ({})
  property bool loading: false
  property bool functions: true     // the engine has stored functions (not SQLite)
  signal openRequested(string schema, string table, string kind)
  signal actionRequested(string action, string schema, string name, string kind)
  signal refreshRequested()
  signal newTableRequested(string schema)

  color: Qt.lighter(Commons.Color.background, 1.05)

  readonly property var groups: [
    { key: "tables", label: "Tables", icon: "", open: true },
    { key: "views", label: "Views", icon: "", open: true },
    { key: "functions", label: "Functions", icon: "ƒ", open: false },
    { key: "sequences", label: "Sequences", icon: "#", open: false }]

  readonly property var rows: {
    collapsed
    const out = []
    const f = filter.toLowerCase()
    const many = tree.length > 1
    for (const s of tree) {
      const sk = "s:" + s.name
      if (many) out.push({ kind: "schema", name: s.name, schema: s.name, id: sk })
      if (many && collapsed[sk]) continue
      for (const g of groups) {
        const items = (s[g.key] || []).filter(function(n) { return !f || n.toLowerCase().indexOf(f) >= 0 })
        if (!items.length) continue
        const gk = "g:" + s.name + ":" + g.key
        const shut = gk in collapsed ? collapsed[gk] : !g.open && !f
        out.push({ kind: "group", name: g.label + "  " + items.length, schema: s.name, id: gk, depth: many ? 1 : 0, shut: shut })
        if (shut) continue
        for (const n of items)
          out.push({ kind: g.key, name: n, schema: s.name, id: s.name + "." + n, icon: g.icon, depth: many ? 2 : 1 })
      }
    }
    return out
  }

  function toggle(id, current) {
    const c = Object.assign({}, collapsed)
    c[id] = !current
    collapsed = c
  }

  ColumnLayout {
    anchors.fill: parent
    anchors.margins: 8
    spacing: 6

    RowLayout {
      Layout.fillWidth: true
      spacing: 4
      Ui.TextField {
        id: search
        readOnly: !activeFocus   // an unfocused field reads no clipboard
        Layout.fillWidth: true
        placeholderText: "Search tables"
        font.family: Style.font.family
        onTextChanged: side.filter = text
        Keys.onDownPressed: list.forceActiveFocus()
        Keys.onReturnPressed: {
          const first = side.rows.find(function(r) { return r.kind === "tables" || r.kind === "views" })
          if (first) side.openRequested(first.schema, first.name, first.kind)
        }
      }
      Ui.Button {
        id: addBtn
        text: "+"
        tooltipText: "New table, view or function"
        onClicked: {
          const p = addBtn.mapToItem(side, 0, addBtn.height)
          menu.item = { kind: "add", name: "", schema: "" }
          menu.x = Math.min(p.x, side.width - menu.width - 4)
          menu.y = p.y
          menu.open()
        }
      }
      Ui.Button { text: "\uf021"; tooltipText: "Reload the schema"; onClicked: side.refreshRequested() }
    }

    ListView {
      id: list
      Layout.fillWidth: true
      Layout.fillHeight: true
      clip: true
      model: side.rows
      boundsBehavior: Flickable.StopAtBounds
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

      delegate: Rectangle {
        required property var modelData
        readonly property bool openable: modelData.kind === "tables" || modelData.kind === "views"
        width: list.width
        height: Math.round(Style.font.body * 1.9)
        radius: 4
        color: modelData.id === side.selected ? Qt.lighter(Commons.Color.background, 1.5)
          : rowHover.hovered ? Qt.lighter(Commons.Color.background, 1.25) : "transparent"

        Text {
          textFormat: Text.PlainText
          x: 6 + (modelData.depth || 0) * 14
          anchors.verticalCenter: parent.verticalCenter
          width: parent.width - x - 6
          elide: Text.ElideRight
          text: modelData.kind === "schema" ? (side.collapsed[modelData.id] ? "▸ " : "▾ ") + "  " + modelData.name
            : modelData.kind === "group" ? (modelData.shut ? "▸ " : "▾ ") + modelData.name
            : modelData.icon + "  " + modelData.name
          color: modelData.kind === "group" ? Commons.Color.muted : (parent.openable || modelData.kind === "schema") ? Commons.Color.foreground : Commons.Color.muted
          font.family: Style.font.family
          font.pixelSize: modelData.kind === "group" ? Style.font.bodySmall : Style.font.body
          font.bold: modelData.kind === "schema"
        }
        HoverHandler { id: rowHover }
        MouseArea {
          anchors.fill: parent
          acceptedButtons: Qt.LeftButton | Qt.RightButton
          onClicked: function(mouse) {
            const d = modelData
            if (d.kind === "schema" && mouse.button !== Qt.RightButton) side.toggle(d.id, !!side.collapsed[d.id])
            else if (d.kind === "group") side.toggle(d.id, d.shut)
            else if (mouse.button === Qt.RightButton) {
              side.selected = d.id
              menu.item = d
              const p = mapToItem(side, mouse.x, mouse.y)
              menu.x = Math.min(p.x, side.width - menu.width - 4)
              menu.y = Math.min(p.y, side.height - menu.implicitHeight - 4)
              menu.open()
            } else if (parent.openable) {
              side.selected = d.id
              side.openRequested(d.schema, d.name, d.kind)
            }
          }
        }
      }

      Text {
        textFormat: Text.PlainText
        y: 8
        width: list.width
        visible: side.rows.length === 0
        text: side.loading ? "Loading…" : side.filter ? "No matches" : "No tables"
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }
    }
  }

  Popup {
    id: menu
    property var item: null
    readonly property var actions: !item ? []
      : item.kind === "tables" ? [
          { act: "open", label: "Open" }, { act: "structure", label: "Structure" }, { act: "newtrigger", label: "New trigger…" },
          { act: "export", label: "Export…" }, { act: "import", label: "Import CSV…" },
          { act: "truncate", label: "Truncate…", danger: true }, { act: "drop", label: "Drop table…", danger: true },
          { act: "copy", label: "Copy name" }]
      : item.kind === "views" ? [
          { act: "open", label: "Open" }, { act: "definition", label: "Edit definition" }, { act: "export", label: "Export…" },
          { act: "drop", label: "Drop view…", danger: true }, { act: "copy", label: "Copy name" }]
      : item.kind === "functions" ? [
          { act: "definition", label: "Edit definition" }, { act: "dropfn", label: "Drop function…", danger: true },
          { act: "copy", label: "Copy name" }]
      : item.kind === "add" ? [{ act: "newtable", label: "New table…" }, { act: "newview", label: "New view…" }]
          .concat(side.functions ? [{ act: "newfunction", label: "New function…" }] : [])
      : item.kind === "schema" ? [{ act: "newtable", label: "New table…" }, { act: "newview", label: "New view…" },
          { act: "newfunction", label: "New function…" }, { act: "copy", label: "Copy name" }]
      : [{ act: "copy", label: "Copy name" }]
    padding: 4
    width: 180
    background: Rectangle { color: Commons.Color.menu.background; border.color: Commons.Color.menu.border; border.width: 1 }
    Column {
      width: parent.width
      Repeater {
        model: menu.actions
        delegate: Ui.Button {
          required property var modelData
          width: menu.width - 8
          leftAlign: true
          text: modelData.label
          foreground: modelData.danger ? Commons.Color.urgent : Commons.Color.foreground
          onClicked: {
            const d = menu.item
            menu.close()
            if (modelData.act === "copy") clipboard.copy(d.name)
            else if (modelData.act === "newtable") side.newTableRequested(d.schema)

            else side.actionRequested(modelData.act, d.schema, d.name, d.kind === "views" ? "view" : d.kind === "functions" ? "function" : "table")
          }
        }
      }
    }
  }

  Clipboard { id: clipboard }
}
