import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

// The table grid. Data and staged edits live in the TableTab (`tab`); the
// grid draws them, handles selection, keyboard and in-place editing.
FocusScope {
  id: grid

  property var tab: null
  readonly property int rowHeight: Math.round(Style.font.body * 2)
  readonly property int gutter: 44
  readonly property var vis: tab ? tab.visibleColumns : []
  readonly property var widths: tab ? tab.colWidths : []
  readonly property var xs: {
    const out = []
    let x = gutter
    for (let i = 0; i < vis.length; i++) { out.push(x); x += widths[i] || 120 }
    out.push(x)
    return out
  }
  readonly property int totalWidth: xs.length ? xs[xs.length - 1] : gutter
  property bool editing: editor.visible

  function ensureVisible(r, vi) {
    if (r >= 0) list.positionViewAtIndex(r, ListView.Contain)
    if (vi >= 0 && vi < vis.length) {
      const left = xs[vi] - gutter, right = xs[vi + 1]
      if (left < list.contentX) list.contentX = left
      else if (right > list.contentX + list.width) list.contentX = Math.min(right - list.width, Math.max(0, totalWidth - list.width))
    }
  }

  function beginEdit(r, vi, initial) {
    if (!tab || vi < 0 || vi >= vis.length || r < 0 || r >= tab.rowCount) return
    if (!tab.canEdit(r)) return
    const c = vis[vi]
    const v = tab.cellValue(r, c)
    if (v !== null && typeof v === "object" && v.$t === "bytes") { tab.notify("Binary values cannot be edited here"); return }
    tab.select(r, vi)
    ensureVisible(r, vi)
    editor.row = r
    editor.vi = vi
    editor.text = initial !== undefined ? initial : tab.editText(v)
    editor.visible = true
    editor.forceActiveFocus()
    if (initial === undefined) editor.selectAll()
  }

  function openEditor(r, vi) {
    if (!tab || vi < 0 || vi >= vis.length || r < 0 || r >= tab.rowCount || !tab.canEdit(r)) return
    const v = tab.cellValue(r, vis[vi])
    if (v !== null && typeof v === "object" && v.$t === "bytes") { tab.notify("Binary values cannot be edited here"); return }
    tab.select(r, vi)
    tab.openEditor(r, vis[vi])
  }

  function finishEdit(save) {
    if (!editor.visible) return
    editor.visible = false
    editor.focus = false
    if (save) tab.setCell(editor.row, vis[editor.vi], editor.text)
    grid.forceActiveFocus()
  }

  Keys.onPressed: function(event) {
    if (!tab || editing) return
    const r = tab.selRow, vi = tab.selCol
    const ctrl = event.modifiers & Qt.ControlModifier
    let nr = r, nv = vi
    if (event.key === Qt.Key_Up) nr = Math.max(0, r - 1)
    else if (event.key === Qt.Key_Down) nr = Math.min(tab.rowCount - 1, r + 1)
    else if (event.key === Qt.Key_Left) nv = Math.max(0, vi - 1)
    else if (event.key === Qt.Key_Right) nv = Math.min(vis.length - 1, vi + 1)
    else if (event.key === Qt.Key_PageDown) nr = Math.min(tab.rowCount - 1, r + Math.floor(list.height / rowHeight))
    else if (event.key === Qt.Key_PageUp) nr = Math.max(0, r - Math.floor(list.height / rowHeight))
    else if (event.key === Qt.Key_Home && ctrl) nr = 0
    else if (event.key === Qt.Key_End && ctrl) nr = tab.rowCount - 1
    else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ShiftModifier) && tab.openEditor) { openEditor(r, vi); event.accepted = true; return }
    else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_F2) { beginEdit(r, vi); event.accepted = true; return }
    else if (event.key === Qt.Key_Delete || event.key === Qt.Key_Backspace) { if (r >= 0) tab.toggleDelete(r); event.accepted = true; return }
    else if (ctrl && event.key === Qt.Key_C) { tab.copyCell(); event.accepted = true; return }
    else if (event.text.length === 1 && !ctrl && event.text >= " ") { beginEdit(r, vi, event.text); event.accepted = true; return }
    else return
    event.accepted = true
    if (tab.rowCount === 0) return
    tab.select(Math.max(0, nr), Math.max(0, nv))
    ensureVisible(tab.selRow, tab.selCol)
  }

  // ---- header ----
  Rectangle {
    id: header
    width: parent.width
    height: grid.rowHeight + 4
    color: Qt.lighter(Commons.Color.background, 1.15)
    clip: true
    z: 2

    Item {
      x: -list.contentX
      height: parent.height
      width: grid.totalWidth
      Repeater {
        model: grid.vis.length
        delegate: Item {
          required property int index
          readonly property var col: grid.tab.columns[grid.vis[index]]
          x: grid.xs[index]
          width: grid.widths[index] || 120
          height: header.height
          Text {
            textFormat: Text.PlainText
            anchors.left: parent.left
            anchors.leftMargin: 8
            anchors.right: sortMark.left
            anchors.verticalCenter: parent.verticalCenter
            text: (col.pk ? "\uf084  " : "") + col.name
            color: Commons.Color.foreground
            elide: Text.ElideRight
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            font.bold: true
          }
          Text {
            id: sortMark
            textFormat: Text.PlainText
            anchors.right: parent.right
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: grid.tab.sortMark(col.name)
            color: Commons.Color.accent
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          MouseArea {
            anchors.fill: parent
            anchors.rightMargin: 6
            onClicked: function(mouse) { grid.tab.toggleSort(col.name, mouse.modifiers & Qt.ShiftModifier) }
          }
          HoverHandler { id: headHover }
          ToolTip {
            visible: headHover.hovered
            delay: 600
            y: parent.height + 2
            padding: 6
            contentItem: Text {
              textFormat: Text.PlainText
              text: col.name + "  " + col.type + (col.nullable ? "" : "  NOT NULL")
                + (col.default !== null && col.default !== undefined ? "  DEFAULT " + col.default : "")
              color: Commons.Color.tooltip.text
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
            }
            background: Rectangle { color: Commons.Color.tooltip.background; border.color: Commons.Color.tooltip.border; border.width: 1 }
          }
          Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Qt.lighter(Commons.Color.background, 1.6) }
          MouseArea {
            // drag to resize
            anchors.right: parent.right
            width: 8
            height: parent.height
            cursorShape: Qt.SplitHCursor
            property real startX: 0
            property real startW: 0
            onPressed: function(mouse) { startX = mapToItem(header, mouse.x, 0).x; startW = parent.width }
            onPositionChanged: function(mouse) {
              if (!pressed) return
              grid.tab.resizeColumn(index, startW + mapToItem(header, mouse.x, 0).x - startX)
            }
          }
        }
      }
    }
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.lighter(Commons.Color.background, 1.6) }
  }

  // ---- rows ----
  ListView {
    id: list
    anchors.top: header.bottom
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    clip: true
    model: grid.tab ? grid.tab.rowCount : 0
    contentWidth: grid.totalWidth
    flickableDirection: Flickable.HorizontalAndVerticalFlick
    boundsBehavior: Flickable.StopAtBounds
    keyNavigationEnabled: false
    reuseItems: true
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }

    delegate: Item {
      id: rowItem
      required property int index
      readonly property string state_: { grid.tab.revision; return grid.tab.rowState(index) }
      width: grid.totalWidth
      height: grid.rowHeight

      Rectangle {
        anchors.fill: parent
        color: rowItem.state_ === "deleted" ? Qt.rgba(0.85, 0.25, 0.25, 0.22)
          : rowItem.state_ === "inserted" ? Qt.rgba(0.3, 0.75, 0.35, 0.18)
          : rowItem.index === grid.tab.selRow ? Qt.lighter(Commons.Color.background, 1.35)
          : rowItem.index % 2 ? Qt.lighter(Commons.Color.background, 1.06) : Commons.Color.background
      }
      Text {
        textFormat: Text.PlainText
        width: grid.gutter - 8
        anchors.verticalCenter: parent.verticalCenter
        horizontalAlignment: Text.AlignRight
        text: rowItem.state_ === "inserted" ? "+" : String(grid.tab.offset + rowItem.index + 1)
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }

      Repeater {
        model: grid.vis.length
        delegate: Rectangle {
          required property int index
          readonly property int c: grid.vis[index]
          readonly property var value: { grid.tab.revision; return grid.tab.cellValue(rowItem.index, c) }
          readonly property bool edited: { grid.tab.revision; return grid.tab.isEdited(rowItem.index, c) }
          readonly property bool selected: rowItem.index === grid.tab.selRow && index === grid.tab.selCol
          x: grid.xs[index]
          width: grid.widths[index] || 120
          height: grid.rowHeight
          color: edited ? Qt.rgba(0.95, 0.75, 0.2, 0.25) : "transparent"
          border.width: selected ? 1 : 0
          border.color: Commons.Color.accent
          Text {
            textFormat: Text.PlainText
            anchors.fill: parent
            anchors.leftMargin: 8
            anchors.rightMargin: 8
            verticalAlignment: Text.AlignVCenter
            text: grid.tab.display(value)
            color: value === null ? Commons.Color.muted : Commons.Color.foreground
            font.italic: value === null
            font.strikeout: rowItem.state_ === "deleted"
            elide: Text.ElideRight
            maximumLineCount: 1
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
          Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Qt.lighter(Commons.Color.background, 1.3) }
        }
      }

      MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        function colAt(x) {
          for (let i = 0; i < grid.vis.length; i++) if (x >= grid.xs[i] && x < grid.xs[i + 1]) return i
          return grid.vis.length ? 0 : -1
        }
        onPressed: function(mouse) {
          grid.finishEdit(true)
          grid.forceActiveFocus()
          grid.tab.select(rowItem.index, colAt(mouse.x))
          if (mouse.button === Qt.RightButton) {
            const p = mapToItem(grid, mouse.x, mouse.y)
            menu.x = Math.min(p.x, grid.width - menu.width)
            menu.y = Math.min(p.y, grid.height - menu.height)
            menu.open()
          }
        }
        onDoubleClicked: function(mouse) { grid.beginEdit(rowItem.index, colAt(mouse.x)) }
      }
    }

    Text {
      textFormat: Text.PlainText
      y: 16
      x: 16
      visible: grid.tab && grid.tab.rowCount === 0 && !grid.tab.loading
      text: grid.tab && grid.tab.error ? "" : (grid.tab && grid.tab.emptyText) || "No rows"
      color: Commons.Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }

    Ui.TextField {
      id: editor
      readOnly: !activeFocus   // an unfocused field reads no clipboard
      parent: list.contentItem
      property int row: -1
      property int vi: -1
      visible: false
      z: 5
      x: vi >= 0 ? grid.xs[vi] : 0
      y: row * grid.rowHeight
      width: vi >= 0 ? Math.max(grid.widths[vi] || 120, 160) : 0
      height: grid.rowHeight
      verticalPadding: 0
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      Keys.onReturnPressed: grid.finishEdit(true)
      Keys.onEnterPressed: grid.finishEdit(true)
      Keys.onEscapePressed: grid.finishEdit(false)
      Keys.onTabPressed: {
        const r = row, next = vi + 1
        grid.finishEdit(true)
        if (next < grid.vis.length) grid.beginEdit(r, next)
      }
      onActiveFocusChanged: if (!activeFocus && visible) grid.finishEdit(true)
    }
  }

  // ---- context menu ----
  Popup {
    id: menu
    padding: 4
    width: 200
    background: Rectangle { color: Commons.Color.menu.background; border.color: Commons.Color.menu.border; border.width: 1 }
    Column {
      width: parent.width
      Repeater {
        model: grid.tab && grid.tab.menuItems ? grid.tab.menuItems
          : grid.tab && grid.tab.readOnly
          ? [{ label: "Copy value", act: "copy" }, { label: "Copy row as TSV", act: "copyrow" },
             { label: "Copy row as JSON", act: "copyjson" }]
          : [{ label: "Edit cell", act: "edit" }].concat(grid.tab && grid.tab.openEditor ? [{ label: "Open in editor…", act: "bigedit" }, { label: "Set DEFAULT", act: "default" }] : []).concat([{ label: "Set NULL", act: "null" },
             { label: "Copy value", act: "copy" }, { label: "Copy row as TSV", act: "copyrow" },
             { label: "Copy row as JSON", act: "copyjson" }, { label: "Copy row as INSERT", act: "copyinsert" },
             { label: "Insert row", act: "insert" }, { label: "Delete / restore row", act: "delete" }])
        delegate: Ui.Button {
          required property var modelData
          width: menu.width - 8
          leftAlign: true
          text: modelData.label
          onClicked: {
            menu.close()
            const t = grid.tab
            if (modelData.act === "edit") grid.beginEdit(t.selRow, t.selCol)
            else if (modelData.act === "null") t.setCell(t.selRow, grid.vis[t.selCol], null)
            else if (modelData.act === "bigedit") grid.openEditor(t.selRow, t.selCol)
            else if (modelData.act === "default") t.setCell(t.selRow, grid.vis[t.selCol], { $t: "default" })
            else if (modelData.act === "copy") t.copyCell()
            else if (modelData.act === "copyrow") t.copyRow()
            else if (modelData.act === "copyjson") t.copyRowJson()
            else if (modelData.act === "copyinsert") t.copyInsert()
            else if (modelData.act === "insert") t.addRow()
            else if (modelData.act === "delete") t.toggleDelete(t.selRow)
            else if (t.menuAction) t.menuAction(modelData.act)
          }
        }
      }
    }
  }
}
