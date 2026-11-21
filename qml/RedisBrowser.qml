import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "Values.js" as Values

// A Redis or Valkey connection: keys on the left (SCAN with a pattern and a
// type), the selected key's value on the right, and a console below.
Item {
  id: rb

  property var helper: null
  property var info: null
  property string toast: ""
  property var keys: []
  property string cursor: "0"
  property int total: 0
  property bool loading: false
  property var selected: null
  property var keyspace: ({})
  property bool consoleOpen: false
  readonly property bool dirty: value.dirty
  readonly property var typeColors: value.typeColors
  readonly property var types: [{ value: "", label: "All types" }, "string", "hash", "list", "set", "zset", "stream"]
  signal closeRequested()

  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b) }

  // user:2 before user:10
  function natural(a, b) {
    const x = a.match(/\d+|\D+/g) || [], y = b.match(/\d+|\D+/g) || []
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      if (x[i] === y[i]) continue
      const nx = /^\d/.test(x[i]), ny = /^\d/.test(y[i])
      if (nx && ny && Number(x[i]) !== Number(y[i])) return Number(x[i]) - Number(y[i])
      return x[i] < y[i] ? -1 : 1
    }
    return x.length - y.length
  }

  function scan(reset) {
    if (loading) return
    loading = true
    const params = { session: info.session, pattern: patternField.text.trim() || "*", cursor: reset ? "0" : cursor, count: 500 }
    if (typeBox.value) params.type = typeBox.value
    helper.request("redis.scan", params, function(r, e) {
      loading = false
      if (e) { toast = e.message; return }
      keys = (reset ? [] : keys).concat(r.keys.sort(function(a, b) { return rb.natural(Values.escaped(a.key), Values.escaped(b.key)) }))
      cursor = r.cursor
      total = r.total
    })
    loadKeyspace()
  }

  function loadKeyspace() {
    helper.request("redis.keyspace", { session: info.session }, function(r, e) { if (!e) keyspace = r })
  }

  // Run proceed() once the open value has no unsaved edits, after asking.
  function whenClean(proceed) {
    if (!value.dirty) { proceed(); return }
    confirm.ask({ title: "Discard your edits to " + value.keyLabel(selected) + "?", text: "The value has not been saved.",
                  confirm: "Discard", danger: true }, function(dlg) {
      dlg.done()
      value.stringDirty = false
      proceed()
    })
  }

  function select(k) {
    if (same(k, selected)) return
    whenClean(function() { selected = k })
  }

  function switchDatabase(db) {
    if (value.dirty) { titleBar.resetDatabase(); whenClean(function() { switchDatabase(db) }); return }
    helper.request("session.use", { session: info.session, database: db }, function(r, e) {
      if (e) { toast = e.message; titleBar.resetDatabase(); return }
      followDatabase(r.database)
    })
  }

  function followDatabase(db) {
    if (db === info.database) return
    info = Object.assign({}, info, { database: db })
    selected = null
    scan(true)
  }

  function updateKey(k, fields) {
    const i = keys.findIndex(function(x) { return rb.same(x.key, k) })
    if (i < 0) return
    let differs = false
    for (const f in fields) if (!same(keys[i][f], fields[f])) differs = true
    if (!differs) return
    const copy = keys.slice()
    copy[i] = Object.assign({}, keys[i], fields)
    const y = keyList.contentY  // a new model array scrolls the list to the top
    keys = copy
    keyList.contentY = y
  }

  function ttlShort(t) {
    if (t === undefined || t < 0) return ""
    if (t < 120) return t + "s"
    if (t < 7200) return Math.round(t / 60) + "m"
    if (t < 172800) return Math.round(t / 3600) + "h"
    return Math.round(t / 86400) + "d"
  }

  // redis-cli style replies
  function fmt(v, pad) {
    pad = pad || ""
    if (v === null || v === undefined) return "(nil)"
    if (typeof v === "number") return Number.isInteger(v) ? "(integer) " + v : String(v)
    if (Array.isArray(v)) {
      if (!v.length) return "(empty array)"
      const w = String(v.length).length + 2
      return v.map(function(x, i) {
        const label = (String(i + 1) + ")").padStart(w - 1) + " "
        const inner = fmt(x, pad + " ".repeat(w))
        return (i ? pad : "") + label + inner
      }).join("\n")
    }
    if (typeof v === "boolean") return v ? "(true)" : "(false)"
    if (typeof v === "object" && v.$t === "double") return "(double) " + v.v
    if (typeof v === "object" && v.$t === "bytes") return JSON.stringify(Values.escaped(v))
    const s = String(v)
    if (s === "OK" || s === "PONG" || s === "QUEUED") return s
    return s.indexOf("\n") >= 0 ? s : JSON.stringify(s)
  }

  Component.onCompleted: scan(true)

  Shortcut { sequence: "Ctrl+F"; enabled: rb.visible; onActivated: patternField.forceActiveFocus() }
  Shortcut { sequence: "Ctrl+`"; enabled: rb.visible; onActivated: rb.consoleOpen = !rb.consoleOpen }
  Shortcut { sequence: "Ctrl+N"; enabled: rb.visible; onActivated: newKey.show() }

  ColumnLayout {
    anchors.fill: parent
    spacing: 0

    TitleBar {
      id: titleBar
      Layout.fillWidth: true
      info: rb.info
      showDatabases: true
      databaseOptions: rb.info ? rb.info.databases.map(function(d) {
        const n = rb.keyspace[d]
        return { value: d, label: "db" + d + (n ? "  ·  " + n.toLocaleString(Qt.locale(), "f", 0) + " keys" : "") }
      }) : []
      onCloseRequested: rb.closeRequested()
      onDatabaseChosen: function(db) { rb.switchDatabase(db) }
      Ui.Button { bordered: true; text: "+ Key"; tooltipText: "New key (Ctrl+N)"; onClicked: newKey.show() }
      Ui.Button { bordered: rb.consoleOpen; text: "Console"; tooltipText: "Run commands (Ctrl+`)"; onClicked: rb.consoleOpen = !rb.consoleOpen }
    }

    SplitView {
      Layout.fillWidth: true
      Layout.fillHeight: true
      orientation: Qt.Vertical

      RowLayout {
        SplitView.fillHeight: true
        spacing: 0

        // ---- keys ----
        Rectangle {
          Layout.preferredWidth: 320
          Layout.fillHeight: true
          color: Qt.lighter(Commons.Color.background, 1.05)
          ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            RowLayout {
              Layout.fillWidth: true
              spacing: 4
              Ui.TextField {
                id: patternField
                readOnly: !activeFocus   // an unfocused field reads no clipboard
                Layout.fillWidth: true
                placeholderText: "Pattern, e.g. user:*"
                font.family: "monospace"
                Keys.onReturnPressed: rb.scan(true)
                Keys.onEnterPressed: rb.scan(true)
              }
              Ui.Button { text: ""; tooltipText: "Scan again"; onClicked: rb.scan(true) }
            }
            Ui.Dropdown {
              id: typeBox
              Layout.fillWidth: true
              showLabel: false
              options: rb.types
              value: ""
              onChanged: function(v) { value = v; rb.scan(true) }
            }
            ListView {
              id: keyList
              Layout.fillWidth: true
              Layout.fillHeight: true
              clip: true
              model: rb.keys
              boundsBehavior: Flickable.StopAtBounds
              ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
              delegate: Rectangle {
                id: keyRow
                required property var modelData
                width: keyList.width
                height: Math.round(Style.font.body * 1.9)
                radius: 4
                color: rb.same(modelData.key, rb.selected) ? Qt.lighter(Commons.Color.background, 1.5)
                  : keyHover.hovered ? Qt.lighter(Commons.Color.background, 1.25) : "transparent"
                HoverHandler { id: keyHover }
                MouseArea { anchors.fill: parent; onClicked: rb.select(keyRow.modelData.key) }
                RowLayout {
                  x: 6
                  width: parent.width - 12
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: 6
                  Rectangle {
                    implicitWidth: Math.round(Style.font.bodySmall * 3.6)
                    implicitHeight: badge.implicitHeight + 2
                    radius: 3
                    color: rb.typeColors[keyRow.modelData.type] || "#777"
                    Text { textFormat: Text.PlainText; id: badge; anchors.centerIn: parent; text: ({ string: "STR", stream: "STRM" })[keyRow.modelData.type] || keyRow.modelData.type.substring(0, 4).toUpperCase(); color: "white"; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall - 2; font.bold: true }
                  }
                  Text {
                    textFormat: Text.PlainText
                    Layout.fillWidth: true
                    text: Values.escaped(keyRow.modelData.key)
                    elide: Text.ElideMiddle
                    color: Commons.Color.foreground
                    font.family: "monospace"
                    font.pixelSize: Style.font.bodySmall
                  }
                  Text {
                    textFormat: Text.PlainText
                    text: rb.ttlShort(keyRow.modelData.ttl)
                    color: Commons.Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.bodySmall
                  }
                }
              }
              Text {
                textFormat: Text.PlainText
                y: 8
                width: keyList.width
                visible: rb.keys.length === 0
                wrapMode: Text.Wrap
                text: rb.loading ? "Scanning…" : rb.cursor !== "0" ? "No matches yet; load more to keep scanning" : "No keys"
                color: Commons.Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
            }
            RowLayout {
              Layout.fillWidth: true
              Text {
                textFormat: Text.PlainText
                Layout.fillWidth: true
                text: rb.keys.length.toLocaleString(Qt.locale(), "f", 0) + " shown · " + rb.total.toLocaleString(Qt.locale(), "f", 0) + " in db" + (rb.info ? rb.info.database : "")
                elide: Text.ElideRight
                color: Commons.Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
              }
              Ui.Button { visible: rb.cursor !== "0"; text: rb.loading ? "Scanning…" : "Load more"; fontSize: Style.font.bodySmall; onClicked: rb.scan(false) }
            }
          }
        }
        Rectangle { Layout.fillHeight: true; implicitWidth: 1; color: Qt.lighter(Commons.Color.background, 1.5) }

        RedisValue {
          id: value
          Layout.fillWidth: true
          Layout.fillHeight: true
          helper: rb.helper
          sid: rb.info ? rb.info.session : ""
          key: rb.selected
          onNotify: function(text) { rb.toast = text }
          onEdited: if (entry) rb.updateKey(key, { ttl: entry.ttl })
          onEntryChanged: if (entry) rb.updateKey(key, { ttl: entry.ttl, type: entry.type })
          onDeleted: function(k) {
            rb.keys = rb.keys.filter(function(x) { return !rb.same(x.key, k) })
            rb.selected = null
            rb.total = Math.max(0, rb.total - 1)
            rb.loadKeyspace()
            rb.toast = "Deleted " + Values.escaped(k)
          }
          onRenamed: function(from, to) { rb.updateKey(from, { key: to }); rb.selected = to; rb.toast = "Renamed to " + to }
        }
      }

      Console {
        id: console_
        visible: rb.consoleOpen
        SplitView.preferredHeight: 240
        SplitView.minimumHeight: 120
        helper: rb.helper
        sid: rb.info ? rb.info.session : ""
        connId: rb.info ? rb.info.id : ""
        method: "redis.cmd"
        placeholder: "Command, e.g. HGETALL user:1   (Up/Down for history)"
        formatter: function(r) { return rb.fmt(r.reply) }
        onCloseRequested: rb.consoleOpen = false
        onRan: function(r) {
          if (r.database !== rb.info.database) rb.followDatabase(r.database)
          else { if (rb.selected !== null && !value.dirty) value.load(true); rb.loadKeyspace() }
        }
      }
    }
  }

  // ---- new key ----
  Popup {
    id: newKey
    property string err: ""
    property string type: "string"
    function show() { err = ""; nameField.setText(patternField.text.indexOf("*") > 0 ? patternField.text.split("*")[0] : ""); firstField.setText(""); valueArea.text = ""; ttlField.setText(""); open() }
    function create() {
      const t = type, a = firstField.text, b = valueArea.text
      let v
      if (t === "string") v = b
      else if (t === "list" || t === "set") v = [b]
      else if (t === "zset") v = [[b, a || "0"]]
      else v = [[a, b]]
      if (t !== "string" && t !== "list" && t !== "set" && !a && t !== "zset") { err = "The first field needs a name"; return }
      const ttl = ttlField.text.trim()
      if (ttl && !/^\d+$/.test(ttl)) { err = "TTL must be a whole number of seconds"; return }
      helper.request("redis.create", { session: rb.info.session, key: nameField.text, type: t, value: v, ttl: ttl ? parseInt(ttl) : null }, function(r, e) {
        if (e) { newKey.err = e.message; return }
        newKey.close()
        const k = nameField.text
        rb.keys = [{ key: k, type: t, ttl: ttl ? parseInt(ttl) : -1 }].concat(rb.keys.filter(function(x) { return !rb.same(x.key, k) }))
        rb.total++
        rb.selected = k
        rb.loadKeyspace()
        rb.toast = "Created " + k
      })
    }
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(parent ? parent.width - 80 : 600, 600)
    height: Math.min(parent ? parent.height - 80 : 520, 500)
    padding: 16
    modal: true
    focus: true
    background: Rectangle { color: Commons.Color.popups.background; border.color: Commons.Color.popups.border; border.width: 1 }
    onOpened: nameField.focusInput()
    ColumnLayout {
      anchors.fill: parent
      spacing: 10
      Text { textFormat: Text.PlainText; text: "New key in db" + (rb.info ? rb.info.database : ""); color: Commons.Color.foreground; font.family: Style.font.family; font.pixelSize: Style.font.body; font.bold: true }
      RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Field { id: nameField; Layout.fillWidth: true; label: "Key" }
        Ui.Dropdown {
          Layout.preferredWidth: 140
          Layout.alignment: Qt.AlignBottom
          showLabel: false
          options: ["string", "hash", "list", "set", "zset", "stream"]
          value: newKey.type
          onChanged: function(v) { newKey.type = v }
        }
        Field { id: ttlField; Layout.preferredWidth: 110; label: "TTL (seconds)"; placeholder: "none" }
      }
      Field {
        id: firstField
        Layout.fillWidth: true
        visible: newKey.type === "hash" || newKey.type === "zset" || newKey.type === "stream"
        label: newKey.type === "zset" ? "Score" : "Field"
        placeholder: newKey.type === "zset" ? "0" : ""
      }
      Text {
        textFormat: Text.PlainText
        text: newKey.type === "string" ? "Value" : newKey.type === "set" || newKey.type === "zset" ? "First member" : "First value"
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
      ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        TextArea {
          id: valueArea
          readOnly: !activeFocus   // an unfocused field reads no clipboard
          selectByMouse: true
          wrapMode: TextEdit.WrapAnywhere
          color: Commons.Color.foreground
          font.family: "monospace"
          font.pixelSize: Style.font.body
          background: Rectangle { color: Qt.lighter(Commons.Color.background, 1.2); border.color: valueArea.activeFocus ? Commons.Color.accent : Qt.lighter(Commons.Color.background, 1.6); radius: 4 }
        }
      }
      Text {
        textFormat: Text.PlainText
        Layout.fillWidth: true
        visible: newKey.type !== "string"
        text: "Add more items after creating the key."
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
      Text { textFormat: Text.PlainText; Layout.fillWidth: true; visible: newKey.err !== ""; text: newKey.err; wrapMode: Text.Wrap; color: Commons.Color.urgent; font.family: "monospace"; font.pixelSize: Style.font.bodySmall }
      RowLayout {
        Layout.fillWidth: true
        Item { Layout.fillWidth: true }
        Ui.Button { text: "Cancel"; onClicked: newKey.close() }
        Ui.Button { bordered: true; foreground: Commons.Color.accent; text: "Create"; enabled: nameField.text !== ""; onClicked: newKey.create() }
      }
    }
  }

  Confirm { id: confirm }
  Toast { message: rb.toast; onExpired: rb.toast = "" }
}
