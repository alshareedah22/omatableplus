import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "Values.js" as Values

// One Redis key: header (name, type, size, TTL) and an editor for its value
// that fits the type. Every change goes to the server at once; list edits
// carry the value they replace so a concurrent change is refused.
Item {
  id: pane

  property var helper: null
  property string sid: ""
  property var key: null
  property var entry: null
  property bool loading: false
  property string error: ""
  property string itemFilter: ""
  property bool stringDirty: false
  readonly property bool dirty: stringDirty
  readonly property var typeColors: ({ string: "#5b9bd5", hash: "#a77fd8", list: "#59b37f", set: "#e0a352",
                                       zset: "#e06c75", stream: "#4fb3bf" })
  signal notify(string text)
  signal edited()                         // size or TTL changed: the key list may refresh it
  signal deleted(var key)
  signal renamed(var oldKey, var newKey)

  function isBinary(v) { return v !== null && typeof v === "object" && v.$t === "bytes" }
  function keyLabel(k) { return Values.escaped(k) }

  function ttlText(t) {
    if (t === undefined || t === null || t < 0) return "no expiry"
    if (t < 120) return t + " s"
    if (t < 7200) return Math.round(t / 60) + " min"
    if (t < 172800) return Math.round(t / 3600) + " h"
    return Math.round(t / 86400) + " days"
  }

  function load(keepText) {
    if (key === null) { entry = null; return }
    loading = true
    error = ""
    const asked = key
    helper.request("redis.get", { session: sid, key: key }, function(r, e) {
      if (asked !== pane.key) return
      loading = false
      if (e) { error = e.message; entry = null; return }
      entry = r
      if (!keepText || !stringDirty) {
        stringDirty = false
        if (r.type === "string") stringArea.text = isBinary(r.value) ? Values.escaped(r.value) : r.value
      }
    })
  }

  function edit(params, done) {
    helper.request("redis.edit", Object.assign({ session: sid, key: key, type: entry.type }, params), function(r, e) {
      if (e) {
        if (done) done(e.message); else notify(e.message)
        if (e.code === "conflict" || e.code === "notfound") load(true)
        return
      }
      if (done) done("")
      load(true)
      pane.edited()
    })
  }

  function saveString() {
    if (!entry || entry.type !== "string" || !entry.editable || isBinary(entry.value)) return
    edit({ action: "set", value: stringArea.text }, function(err) {
      if (err) { notify(err); return }
      stringDirty = false
      notify("Saved " + keyLabel(key))
    })
  }

  function formatJson() {
    try { stringArea.text = JSON.stringify(JSON.parse(stringArea.text), null, 2); stringDirty = true }
    catch (err) { notify("Not valid JSON: " + err.message) }
  }

  // Rows for the item list: { a, b, item, old, raw }
  readonly property var rows: {
    if (!entry || entry.value === null || entry.type === "string") return []
    const f = itemFilter.toLowerCase()
    const out = []
    const v = entry.value
    for (let i = 0; i < v.length; i++) {
      let row
      if (entry.type === "hash") row = { a: v[i][0], b: v[i][1] }
      else if (entry.type === "list") row = { a: i, b: v[i] }
      else if (entry.type === "set") row = { a: v[i], b: null }
      else if (entry.type === "zset") row = { a: v[i][1], b: v[i][0] }
      else row = { a: v[i].id, b: v[i].fields.map(function(p) { return Values.display(p[0]) + "=" + Values.display(p[1]) }).join("  ") }
      row.index = i
      if (f && (Values.display(row.a) + " " + Values.display(row.b)).toLowerCase().indexOf(f) < 0) continue
      out.push(row)
    }
    return out
  }
  readonly property var headers: !entry ? ["", ""] : ({ hash: ["Field", "Value"], list: ["Index", "Value"], set: ["Member", ""],
                                                      zset: ["Score", "Member"], stream: ["ID", "Fields"] })[entry.type] || ["", ""]

  function openItem(row) {
    if (row && (isBinary(row.a) && entry.type !== "list" && entry.type !== "zset" || isBinary(row.b))) {
      notify("Binary items cannot be edited here; use the console")
      return
    }
    itemDlg.row = row
    itemDlg.err = ""
    const t = entry.type
    let first = "", second = ""
    if (t === "hash") { first = row ? row.a : ""; second = row ? row.b : "" }
    else if (t === "list") { first = row ? String(row.a) : ""; second = row ? row.b : "" }
    else if (t === "set") second = row ? row.a : ""
    else if (t === "zset") { first = row ? String(row.a) : "0"; second = row ? row.b : "" }
    else second = "field=value"
    firstField.setText(first)
    secondArea.text = second
    itemDlg.open()
  }

  function saveItem() {
    const t = entry.type, row = itemDlg.row, a = firstField.text, b = secondArea.text
    const done = function(err) { if (err) itemDlg.err = err; else itemDlg.close() }
    if (t === "hash") edit({ action: "set", item: a, value: b, old: row ? row.a : null }, done)
    else if (t === "list") edit(row ? { action: "set", item: row.index, value: b, old: entry.value[row.index] } : { action: "push", value: b }, done)
    else if (t === "set") edit(row ? { action: "set", value: b, old: row.a } : { action: "add", value: b }, done)
    else if (t === "zset") edit({ action: "set", item: b, score: a, old: row ? row.b : null }, done)
    else {
      const pairs = []
      for (const ln of b.split("\n")) {
        if (!ln.trim()) continue
        const at = ln.indexOf("=")
        if (at <= 0) { itemDlg.err = "Write one field=value per line"; return }
        pairs.push([ln.substring(0, at).trim(), ln.substring(at + 1)])
      }
      edit({ action: "add", value: pairs }, done)
    }
  }

  function deleteItem(row) {
    const t = entry.type
    const label = t === "zset" ? Values.display(row.b) : Values.display(row.a)
    confirm.ask({ title: "Remove " + (t === "list" ? "item " + row.a : label) + "?", text: "From " + keyLabel(key) + ". This cannot be undone.",
                  confirm: "Remove", danger: true }, function(dlg) {
      const p = t === "hash" ? { action: "delete", item: row.a, confirmed: true }
        : t === "list" ? { action: "delete", item: row.index, old: entry.value[row.index], confirmed: true }
        : t === "set" ? { action: "delete", item: row.a, confirmed: true }
        : t === "zset" ? { action: "delete", item: row.b, confirmed: true }
        : { action: "delete", item: row.a, confirmed: true }
      edit(p, function(err) { if (err) dlg.fail(err); else dlg.done() })
    })
  }

  function deleteKey() {
    confirm.ask({ title: "Delete " + keyLabel(key) + "?", text: "The key and its whole value are deleted. This cannot be undone.",
                  confirm: "Delete key", danger: true }, function(dlg) {
      helper.request("redis.del", { session: sid, keys: [key], confirmed: true }, function(r, e) {
        if (e) { dlg.fail(e.message); return }
        dlg.done()
        const k = key
        stringDirty = false
        pane.deleted(k)
      })
    })
  }

  onKeyChanged: { stringDirty = false; itemFilter = ""; filterField.text = ""; load(false) }

  Shortcut { sequence: "Ctrl+S"; enabled: pane.visible && pane.stringDirty; onActivated: pane.saveString() }

  Text {
    textFormat: Text.PlainText
    anchors.centerIn: parent
    visible: pane.key === null
    text: "Pick a key on the left"
    color: Commons.Color.muted
    font.family: Style.font.family
    font.pixelSize: Style.font.body
  }

  ColumnLayout {
    anchors.fill: parent
    anchors.margins: 10
    spacing: 8
    visible: pane.key !== null

    // ---- header ----
    RowLayout {
      Layout.fillWidth: true
      spacing: 8
      Rectangle {
        visible: pane.entry !== null
        implicitWidth: typeText.implicitWidth + 10
        implicitHeight: typeText.implicitHeight + 4
        radius: 3
        color: pane.entry ? (pane.typeColors[pane.entry.type] || "#777") : "transparent"
        Text { textFormat: Text.PlainText; id: typeText; anchors.centerIn: parent; text: pane.entry ? pane.entry.type.toUpperCase() : ""; color: "white"; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; font.bold: true }
      }
      TextEdit {
        Layout.fillWidth: true
        readOnly: true
        selectByMouse: true
        text: pane.key === null ? "" : pane.keyLabel(pane.key)
        color: Commons.Color.foreground
        font.family: "monospace"
        font.pixelSize: Style.font.body
        font.bold: true
        wrapMode: TextEdit.WrapAnywhere
      }
      Ui.Button { text: "Rename"; enabled: pane.entry !== null && !pane.isBinary(pane.key); onClicked: { renameField.text = pane.keyLabel(pane.key); renameDlg.err = ""; renameDlg.open() } }
      Ui.Button { text: ""; tooltipText: "Reload"; onClicked: pane.load(false) }
      Ui.Button { text: "Delete"; foreground: Commons.Color.urgent; enabled: pane.entry !== null; onClicked: pane.deleteKey() }
    }
    RowLayout {
      Layout.fillWidth: true
      visible: pane.entry !== null
      spacing: 12
      Text {
        textFormat: Text.PlainText
        text: !pane.entry ? "" : pane.entry.type === "string" ? pane.entry.size.toLocaleString(Qt.locale(), "f", 0) + " bytes"
          : pane.entry.size.toLocaleString(Qt.locale(), "f", 0) + (pane.entry.size === 1 ? " item" : " items")
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
      Text {
        textFormat: Text.PlainText
        text: pane.entry ? "TTL: " + pane.ttlText(pane.entry.ttl) : ""
        color: pane.entry && pane.entry.ttl >= 0 ? Commons.Color.foreground : Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
      Ui.Button { text: "Set TTL…"; fontSize: Style.font.bodySmall; onClicked: { ttlField.text = pane.entry && pane.entry.ttl > 0 ? String(pane.entry.ttl) : ""; ttlDlg.err = ""; ttlDlg.open() } }
      Item { Layout.fillWidth: true }
      Ui.TextField {
        id: filterField
        readOnly: !activeFocus   // an unfocused field reads no clipboard
        visible: pane.entry !== null && pane.entry.type !== "string"
        Layout.preferredWidth: 200
        placeholderText: "Filter items"
        font.family: Style.font.family
        onTextChanged: pane.itemFilter = text
      }
      Ui.Button {
        visible: pane.entry !== null && pane.entry.type !== "string" && pane.entry.value !== null
        bordered: true
        text: !pane.entry ? "" : ({ hash: "+ Field", list: "+ Push", set: "+ Member", zset: "+ Member", stream: "+ Entry" })[pane.entry.type] || "+ Item"
        onClicked: pane.openItem(null)
      }
    }

    Text {
      textFormat: Text.PlainText
      Layout.fillWidth: true
      visible: pane.error !== ""
      text: pane.error
      wrapMode: Text.Wrap
      color: Commons.Color.urgent
      font.family: "monospace"
      font.pixelSize: Style.font.bodySmall
    }
    Text {
      textFormat: Text.PlainText
      Layout.fillWidth: true
      visible: pane.entry !== null && (pane.entry.truncated || (pane.entry.type === "string" && pane.isBinary(pane.entry.value)) || pane.entry.value === null)
      wrapMode: Text.Wrap
      text: !pane.entry ? "" : pane.entry.value === null ? "OmaTablePlus cannot show " + pane.entry.type + " values yet; use the console."
        : pane.entry.type === "string" && pane.entry.truncated ? "Showing the first 1 MB, read-only. Use the console for the rest."
        : pane.entry.type === "string" ? "Binary value, shown with \\xHH escapes and read-only. Use the console to change it."
        : "Showing the first " + pane.entry.value.length.toLocaleString(Qt.locale(), "f", 0) + " of " + pane.entry.size.toLocaleString(Qt.locale(), "f", 0) + " items."
      color: Commons.Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    // ---- string ----
    ScrollView {
      Layout.fillWidth: true
      Layout.fillHeight: true
      visible: pane.entry !== null && pane.entry.type === "string"
      TextArea {
        id: stringArea
        readonly property bool locked: !pane.entry || !pane.entry.editable || pane.isBinary(pane.entry.value)
        readOnly: !activeFocus || locked   // an unfocused field reads no clipboard
        selectByMouse: true
        wrapMode: TextEdit.WrapAnywhere
        color: Commons.Color.foreground
        font.family: "monospace"
        font.pixelSize: Style.font.body
        background: Rectangle { color: Qt.lighter(Commons.Color.background, 1.1); border.color: stringArea.activeFocus ? Commons.Color.accent : "transparent"; radius: 4 }
        onTextChanged: if (activeFocus) pane.stringDirty = true
      }
    }
    RowLayout {
      Layout.fillWidth: true
      visible: pane.entry !== null && pane.entry.type === "string" && !stringArea.locked
      Ui.Button { text: "Format JSON"; onClicked: pane.formatJson() }
      Item { Layout.fillWidth: true }
      Text { textFormat: Text.PlainText; visible: pane.stringDirty; text: "Unsaved"; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
      Ui.Button { text: "Revert"; enabled: pane.stringDirty; onClicked: { pane.stringDirty = false; pane.load(false) } }
      Ui.Button { bordered: true; foreground: Commons.Color.accent; text: "Save (Ctrl+S)"; enabled: pane.stringDirty; onClicked: pane.saveString() }
    }

    // ---- collections ----
    Rectangle {
      Layout.fillWidth: true
      implicitHeight: headRow.implicitHeight + 8
      visible: pane.entry !== null && pane.entry.type !== "string" && pane.entry.value !== null
      color: Qt.lighter(Commons.Color.background, 1.12)
      RowLayout {
        id: headRow
        x: 8
        width: parent.width - 16
        anchors.verticalCenter: parent.verticalCenter
        Text { textFormat: Text.PlainText; Layout.preferredWidth: Math.round(itemList.width * (pane.entry && pane.entry.type === "set" ? 0.85 : 0.32)); text: pane.headers[0]; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; font.bold: true }
        Text { textFormat: Text.PlainText; Layout.fillWidth: true; text: pane.headers[1]; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; font.bold: true }
      }
    }
    ListView {
      id: itemList
      Layout.fillWidth: true
      Layout.fillHeight: true
      visible: pane.entry !== null && pane.entry.type !== "string" && pane.entry.value !== null
      clip: true
      model: pane.rows
      boundsBehavior: Flickable.StopAtBounds
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
      delegate: Rectangle {
        id: itemRow
        required property var modelData
        required property int index
        width: itemList.width
        height: Math.max(Math.round(Style.font.body * 2), bText.implicitHeight + 10)
        color: rowHover.hovered ? Qt.lighter(Commons.Color.background, 1.2) : index % 2 ? Qt.lighter(Commons.Color.background, 1.04) : "transparent"
        HoverHandler { id: rowHover }
        MouseArea { anchors.fill: parent; onDoubleClicked: if (pane.entry.type !== "stream") pane.openItem(itemRow.modelData) }
        RowLayout {
          x: 8
          width: parent.width - 16
          anchors.verticalCenter: parent.verticalCenter
          spacing: 8
          Text {
            textFormat: Text.PlainText
            Layout.preferredWidth: Math.round(itemList.width * (pane.entry.type === "set" ? 0.85 : 0.32))
            text: Values.display(itemRow.modelData.a)
            elide: Text.ElideRight
            color: pane.entry.type === "list" || pane.entry.type === "zset" || pane.entry.type === "stream" ? Commons.Color.muted : Commons.Color.foreground
            font.family: "monospace"
            font.pixelSize: Style.font.bodySmall
          }
          Text {
            id: bText
            textFormat: Text.PlainText
            Layout.fillWidth: true
            text: itemRow.modelData.b === null ? "" : Values.display(itemRow.modelData.b)
            maximumLineCount: 3
            wrapMode: Text.WrapAnywhere
            elide: Text.ElideRight
            color: Commons.Color.foreground
            font.family: "monospace"
            font.pixelSize: Style.font.bodySmall
          }
          Ui.Button { visible: rowHover.hovered && pane.entry.type !== "stream"; text: "✎"; fontSize: Style.font.bodySmall; tooltipText: "Edit"; onClicked: pane.openItem(itemRow.modelData) }
          Ui.Button { visible: rowHover.hovered; text: "✕"; fontSize: Style.font.bodySmall; foreground: Commons.Color.urgent; tooltipText: "Remove"; onClicked: pane.deleteItem(itemRow.modelData) }
        }
      }
      Text {
        textFormat: Text.PlainText
        y: 8
        x: 8
        visible: pane.rows.length === 0
        text: pane.loading ? "Loading…" : "No items match"
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }
    }
  }

  // ---- dialogs ----
  Confirm { id: confirm }

  Popup {
    id: itemDlg
    property var row: null
    property string err: ""
    readonly property string type: pane.entry ? pane.entry.type : ""
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(parent ? parent.width - 80 : 600, 620)
    height: Math.min(parent ? parent.height - 80 : 500, 440)
    padding: 16
    modal: true
    focus: true
    background: Rectangle { color: Commons.Color.popups.background; border.color: Commons.Color.popups.border; border.width: 1 }
    onOpened: (itemDlg.type === "hash" && !itemDlg.row ? firstField : secondArea).forceActiveFocus()
    ColumnLayout {
      anchors.fill: parent
      spacing: 10
      Text {
        textFormat: Text.PlainText
        text: (itemDlg.row ? "Edit " : "Add to ") + (pane.key === null ? "" : pane.keyLabel(pane.key))
        color: Commons.Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }
      Field {
        id: firstField
        Layout.fillWidth: true
        visible: itemDlg.type === "hash" || itemDlg.type === "zset" || (itemDlg.type === "list" && itemDlg.row !== null)
        enabled: itemDlg.type !== "list"
        label: itemDlg.type === "hash" ? "Field" : itemDlg.type === "zset" ? "Score" : "Index"
      }
      Text {
        textFormat: Text.PlainText
        text: itemDlg.type === "hash" || itemDlg.type === "list" ? "Value" : itemDlg.type === "stream" ? "Fields, one field=value per line" : "Member"
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
      ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        TextArea {
          id: secondArea
          readOnly: !activeFocus   // an unfocused field reads no clipboard
          selectByMouse: true
          wrapMode: TextEdit.WrapAnywhere
          color: Commons.Color.foreground
          font.family: "monospace"
          font.pixelSize: Style.font.body
          background: Rectangle { color: Qt.lighter(Commons.Color.background, 1.2); border.color: secondArea.activeFocus ? Commons.Color.accent : Qt.lighter(Commons.Color.background, 1.6); radius: 4 }
          Keys.onPressed: function(event) {
            if ((event.modifiers & Qt.ControlModifier) && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_S)) { pane.saveItem(); event.accepted = true }
          }
        }
      }
      Text {
        textFormat: Text.PlainText
        Layout.fillWidth: true
        visible: itemDlg.err !== ""
        text: itemDlg.err
        wrapMode: Text.Wrap
        color: Commons.Color.urgent
        font.family: "monospace"
        font.pixelSize: Style.font.bodySmall
      }
      RowLayout {
        Layout.fillWidth: true
        Item { Layout.fillWidth: true }
        Ui.Button { text: "Cancel"; onClicked: itemDlg.close() }
        Ui.Button { bordered: true; foreground: Commons.Color.accent; text: "Save (Ctrl+Enter)"; onClicked: pane.saveItem() }
      }
    }
  }

  Popup {
    id: renameDlg
    property string err: ""
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(parent ? parent.width - 80 : 500, 480)
    padding: 16
    modal: true
    focus: true
    background: Rectangle { color: Commons.Color.popups.background; border.color: Commons.Color.popups.border; border.width: 1 }
    onOpened: renameField.focusInput()
    function apply() {
      const to = renameField.text
      helper.request("redis.rename", { session: pane.sid, key: pane.key, new: to }, function(r, e) {
        if (e) { renameDlg.err = e.message; return }
        renameDlg.close()
        const from = pane.key
        pane.renamed(from, to)
      })
    }
    ColumnLayout {
      anchors.fill: parent
      spacing: 10
      Field { id: renameField; Layout.fillWidth: true; label: "New key name"; Keys.onReturnPressed: renameDlg.apply() }
      Text { textFormat: Text.PlainText; visible: renameDlg.err !== ""; text: renameDlg.err; color: Commons.Color.urgent; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
      RowLayout {
        Layout.fillWidth: true
        Item { Layout.fillWidth: true }
        Ui.Button { text: "Cancel"; onClicked: renameDlg.close() }
        Ui.Button { bordered: true; foreground: Commons.Color.accent; text: "Rename"; onClicked: renameDlg.apply() }
      }
    }
  }

  Popup {
    id: ttlDlg
    property string err: ""
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(parent ? parent.width - 80 : 400, 380)
    padding: 16
    modal: true
    focus: true
    background: Rectangle { color: Commons.Color.popups.background; border.color: Commons.Color.popups.border; border.width: 1 }
    onOpened: ttlField.focusInput()
    function apply(ttl) {
      if (ttl !== null && !/^\d+$/.test(ttl)) { err = "Seconds must be a whole number"; return }
      helper.request("redis.ttl", { session: pane.sid, key: pane.key, ttl: ttl === null ? null : parseInt(ttl) }, function(r, e) {
        if (e) { ttlDlg.err = e.message; return }
        ttlDlg.close()
        pane.load(true)
        pane.edited()
      })
    }
    ColumnLayout {
      anchors.fill: parent
      spacing: 10
      Field { id: ttlField; Layout.fillWidth: true; label: "Expire after (seconds)"; placeholder: "e.g. 3600"; Keys.onReturnPressed: ttlDlg.apply(ttlField.text.trim()) }
      Text { textFormat: Text.PlainText; visible: ttlDlg.err !== ""; text: ttlDlg.err; color: Commons.Color.urgent; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
      RowLayout {
        Layout.fillWidth: true
        Ui.Button { text: "Remove expiry"; onClicked: ttlDlg.apply(null) }
        Item { Layout.fillWidth: true }
        Ui.Button { text: "Cancel"; onClicked: ttlDlg.close() }
        Ui.Button { bordered: true; foreground: Commons.Color.accent; text: "Set"; onClicked: ttlDlg.apply(ttlField.text.trim()) }
      }
    }
  }
}
