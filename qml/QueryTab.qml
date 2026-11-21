import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

// A SQL query: editor on top, one result tab per statement below, and a
// drawer with the connection's history and the saved queries.
Item {
  id: q

  property var helper: null
  property string sid: ""
  property string engine: ""
  property string connId: ""
  property var palette: ({})
  property var catalog: []
  property string folder: ""        // where the file picker starts
  property string initialText: ""   // a definition or template it opens with
  property bool active: false
  readonly property bool dirty: resultTabs.dirty

  property bool running: false
  property real startedAt: 0
  property real elapsed: 0
  property var results: []
  property string error: ""

  property bool drawerOpen: false
  property string drawerPage: "history"
  property var historyItems: []
  property var savedItems: []
  property string savedId: ""
  property string savedName: ""

  signal notify(string text)
  signal exportRequested(string sql)
  signal ddlRan()
  signal databaseReported(string database)

  function run(all) {
    if (running) return
    if (resultTabs.dirty) { notify("Commit or discard the edits in the result first"); return }
    const text = editor.text
    const params = { session: sid }
    if (!all && editor.selectionEnd > editor.selectionStart) params.text = text.substring(editor.selectionStart, editor.selectionEnd)
    else {
      params.text = text
      if (!all) params.cursor = editor.cursorPosition
    }
    running = true
    startedAt = Date.now()
    elapsed = 0
    error = ""
    results = []
    helper.request("query.run", params, function(r, e) {
      running = false
      if (e) { error = e.message; results = []; return }
      results = r.results
      if (r.ddl) q.ddlRan()
      q.databaseReported(r.database)
      if (drawerOpen && drawerPage === "history") loadHistory()
    })
  }

  readonly property var sqlFiles: [".sql", ".txt"]

  function openFile() {
    picker.pick({ title: "Open a SQL file", extensions: sqlFiles, folder: folder }, function(path) {
      helper.request("file.read", { path: path }, function(r, e) {
        if (e) { notify(e.message); return }
        load(r.text, null)
        savedName = r.path.split("/").pop()
        notify("Opened " + savedName)
      })
    })
  }

  function saveFile(overwrite, path) {
    if (!path) {
      picker.pick({ title: "Save the query to a file", save: true, extensions: sqlFiles, folder: folder,
                    name: (savedName && /\.sql$/i.test(savedName) ? savedName : "query.sql") }, function(p) { saveFile(false, p) })
      return
    }
    helper.request("file.write", { path: path, text: editor.text, overwrite: overwrite }, function(r, e) {
      if (e && e.code === "exists") {
        confirm.ask({ title: "Replace " + path.split("/").pop() + "?", text: "A file with that name already exists in this folder.",
                      confirm: "Replace", danger: true }, function(dlg) { dlg.done(); saveFile(true, path) })
        return
      }
      if (e) { notify(e.message); return }
      notify("Saved to " + r.path)
    })
  }

  function discardAll() { resultTabs.discardEdits() }

  function runFile() {
    picker.pick({ title: "Run a SQL file", extensions: sqlFiles, folder: folder, confirm: "Run" }, function(path) {
      const name = path.split("/").pop()
      running = true
      startedAt = Date.now()
      elapsed = 0
      error = ""
      results = []
      helper.request("query.run_file", { session: sid, path: path }, function(r, e) {
        running = false
        if (e) { if (e.code !== "cancelled") error = e.message; return }
        if (r.ddl) q.ddlRan()
        q.databaseReported(r.database)
        const secs = (r.ms / 1000).toFixed(1) + " s"
        if (r.error) error = name + ": statement " + r.error.statement + " of " + r.total + " failed after " + r.ran
                             + " ran: " + r.error.message + "\n\n" + r.error.sql
        else notify("Ran " + r.total + (r.total === 1 ? " statement" : " statements") + " from " + name
                    + (r.affected ? " (" + r.affected + " rows changed)" : "") + " in " + secs)
        if (drawerOpen && drawerPage === "history") loadHistory()
      })
    })
  }

  function explain() {
    if (editor.selectionEnd > editor.selectionStart) planView.show(editor.text.substring(editor.selectionStart, editor.selectionEnd), null)
    else planView.show(editor.text, editor.cursorPosition)
  }

  function cancel() {
    if (running) helper.request("query.cancel", { session: sid })
  }

  function loadHistory() {
    helper.request("query.history", { conn: connId, search: historySearch.text, limit: 200 }, function(r, e) {
      if (!e) historyItems = r.items
    })
  }

  function loadSaved() {
    helper.request("query.saved", {}, function(r, e) { if (!e) savedItems = r.items })
  }

  function openDrawer(page) {
    drawerPage = page
    drawerOpen = true
    if (page === "history") loadHistory(); else loadSaved()
  }

  function load(sql, saved) {
    editor.replaceAll(sql)
    savedId = saved ? saved.id : ""
    savedName = saved ? saved.name : ""
  }

  function save(name) {
    helper.request("query.save", { name: name, sql: editor.text, id: savedId || null }, function(r, e) {
      if (e) { notify(e.message); return }
      savedId = r.id
      savedName = r.name
      savePopup.close()
      notify("Saved “" + r.name + "”")
      if (drawerOpen && drawerPage === "saved") loadSaved()
    })
  }

  function when(ts) {
    const d = new Date(ts)
    const today = new Date()
    return d.toDateString() === today.toDateString()
      ? d.toLocaleTimeString(Qt.locale(), "HH:mm") : d.toLocaleDateString(Qt.locale(), "d MMM") + " " + d.toLocaleTimeString(Qt.locale(), "HH:mm")
  }

  Timer { interval: 100; repeat: true; running: q.running; onTriggered: q.elapsed = (Date.now() - q.startedAt) / 1000 }

  Shortcut { sequences: ["Ctrl+Return", "Ctrl+Enter"]; enabled: q.active; onActivated: q.run(false) }
  Shortcut { sequences: ["Ctrl+Shift+Return", "Ctrl+Shift+Enter"]; enabled: q.active; onActivated: q.run(true) }
  Shortcut { sequences: [StandardKey.Save]; enabled: q.active; onActivated: savePopup.open() }
  Shortcut { sequence: "Ctrl+H"; enabled: q.active; onActivated: { if (q.drawerOpen) q.drawerOpen = false; else q.openDrawer("history") } }
  Shortcut { sequence: "Ctrl+."; enabled: q.active && q.running; onActivated: q.cancel() }
  Shortcut { sequence: "Ctrl+E"; enabled: q.active && !q.running; onActivated: q.explain() }

  onActiveChanged: if (active) editor.focusEditor()
  Component.onCompleted: {
    if (initialText) editor.replaceAll(initialText)
    editor.focusEditor()
  }

  RowLayout {
    anchors.fill: parent
    spacing: 0

    ColumnLayout {
      Layout.fillWidth: true
      Layout.fillHeight: true
      spacing: 0

      // ---- toolbar ----
      Rectangle {
        Layout.fillWidth: true
        implicitHeight: bar.implicitHeight + 10
        color: Qt.lighter(Commons.Color.background, 1.08)
        clip: true
        RowLayout {
          id: bar
          x: 8
          width: parent.width - 16
          anchors.verticalCenter: parent.verticalCenter
          spacing: 6
          Ui.Button { bordered: true; foreground: Commons.Color.accent; text: "▶ Run"; tooltipText: "Run the statement at the cursor, or the selection (Ctrl+Enter)"; enabled: !q.running; onClicked: q.run(false) }
          Ui.Button { bordered: true; text: "Run all"; tooltipText: "Ctrl+Shift+Enter"; enabled: !q.running; onClicked: q.run(true) }
          Ui.Button { visible: q.running; bordered: true; foreground: Commons.Color.urgent; text: "■ Cancel"; tooltipText: "Ctrl+."; onClicked: q.cancel() }
          Ui.Button { bordered: true; text: "Explain"; tooltipText: "Show how the server runs the statement at the cursor (Ctrl+E)"; onClicked: q.explain() }
          Ui.Button { bordered: true; text: "Format"; tooltipText: "Tidy the SQL, or the selection (Ctrl+Shift+F)"; onClicked: { editor.format(); editor.focusEditor() } }
          Text {
            textFormat: Text.PlainText
            visible: q.running
            text: "Running… " + q.elapsed.toFixed(1) + " s"
            color: Commons.Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          Item { Layout.fillWidth: true }
          Text {
            textFormat: Text.PlainText
            visible: q.savedName !== ""
            text: q.savedName
            color: Commons.Color.muted
            elide: Text.ElideRight
            Layout.fillWidth: true
            Layout.maximumWidth: implicitWidth
            horizontalAlignment: Text.AlignRight
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          Ui.Button {
            id: fileBtn
            bordered: true
            text: "File ▾"
            tooltipText: "Open, save or run a .sql file"
            onClicked: {
              const p = fileBtn.mapToItem(q, 0, fileBtn.height + 2)
              fileMenu.x = Math.min(p.x, q.width - fileMenu.width - 4)
              fileMenu.y = p.y
              fileMenu.open()
            }
          }
          Ui.Button { bordered: true; text: "Save"; tooltipText: "Save this query (Ctrl+S)"; onClicked: savePopup.open() }
          Ui.Button { bordered: true; selected: q.drawerOpen; text: "History"; tooltipText: "History and saved queries (Ctrl+H)"; onClicked: { if (q.drawerOpen) q.drawerOpen = false; else q.openDrawer(q.drawerPage) } }
        }
      }

      SplitView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        orientation: Qt.Vertical
        handle: Rectangle {
          implicitHeight: 5
          color: SplitHandle.pressed || SplitHandle.hovered ? Qt.alpha(Commons.Color.accent, 0.5) : Qt.lighter(Commons.Color.background, 1.3)
        }

        CodeEditor {
          id: editor
          SplitView.preferredHeight: parent ? parent.height * 0.42 : 300
          SplitView.minimumHeight: 80
          engine: q.engine
          palette: q.palette
          catalog: q.catalog
          onRunCurrent: q.run(false)
          onRunAll: q.run(true)
        }

        ResultTabs {
          id: resultTabs
          helper: q.helper
          sid: q.sid
          engine: q.engine
          SplitView.fillHeight: true
          SplitView.minimumHeight: 80
          results: q.results
          placeholder: q.error ? q.error : q.running ? "Running…" : "Ctrl+Enter runs the statement at the cursor (or the selection), Ctrl+Shift+Enter runs them all"
          onNotify: function(text) { q.notify(text) }
          onExportRequested: function(sql) { q.exportRequested(sql) }
        }
      }
    }

    // ---- drawer: history and saved queries ----
    Rectangle {
      Layout.fillHeight: true
      Layout.preferredWidth: Math.min(300, q.width * 0.4)
      visible: q.drawerOpen
      color: Qt.lighter(Commons.Color.background, 1.05)
      Rectangle { width: 1; height: parent.height; color: Qt.lighter(Commons.Color.background, 1.5) }

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        anchors.leftMargin: 9
        spacing: 6

        RowLayout {
          Layout.fillWidth: true
          spacing: 4
          Ui.Button { bordered: q.drawerPage === "history"; text: "History"; onClicked: q.openDrawer("history") }
          Ui.Button { bordered: q.drawerPage === "saved"; text: "Saved"; onClicked: q.openDrawer("saved") }
          Item { Layout.fillWidth: true }
          Ui.Button {
            visible: q.drawerPage === "history" && q.historyItems.length > 0
            text: "Clear"
            tooltipText: "Forget this connection's history"
            onClicked: helper.request("query.history_clear", { conn: q.connId }, function() { q.loadHistory() })
          }
          Ui.Button { text: "✕"; tooltipText: "Close (Ctrl+H)"; onClicked: q.drawerOpen = false }
        }

        Ui.TextField {
          id: historySearch
          readOnly: !activeFocus   // an unfocused field reads no clipboard
          Layout.fillWidth: true
          visible: q.drawerPage === "history"
          placeholderText: "Search history"
          font.family: Style.font.family
          onTextEdited: searchTimer.restart()
          Timer { id: searchTimer; interval: 250; onTriggered: q.loadHistory() }
        }

        ListView {
          id: drawerList
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          spacing: 2
          model: q.drawerPage === "history" ? q.historyItems : q.savedItems
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
          delegate: Rectangle {
            id: entry
            required property var modelData
            width: drawerList.width
            implicitHeight: entryCol.implicitHeight + 10
            radius: 4
            color: entryHover.hovered ? Qt.lighter(Commons.Color.background, 1.25) : "transparent"
            HoverHandler { id: entryHover }
            Column {
              id: entryCol
              x: 6
              y: 5
              width: parent.width - 12 - (delBtn.visible ? delBtn.width : 0)
              spacing: 2
              Text {
                textFormat: Text.PlainText
                visible: q.drawerPage === "saved"
                width: parent.width
                text: entry.modelData.name || ""
                color: Commons.Color.foreground
                elide: Text.ElideRight
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }
              Text {
                textFormat: Text.PlainText
                width: parent.width
                text: String(entry.modelData.sql || "").replace(/\s+/g, " ")
                color: q.drawerPage === "saved" ? Commons.Color.muted : Commons.Color.foreground
                elide: Text.ElideRight
                maximumLineCount: q.drawerPage === "history" ? 2 : 1
                wrapMode: Text.WrapAnywhere
                font.family: "monospace"
                font.pixelSize: Style.font.bodySmall
              }
              Text {
                textFormat: Text.PlainText
                visible: q.drawerPage === "history"
                width: parent.width
                text: q.drawerPage !== "history" ? ""
                  : (entry.modelData.ok ? "✓ " : "✗ ") + q.when(entry.modelData.ts)
                    + (entry.modelData.database ? " · " + entry.modelData.database : "") + " · " + entry.modelData.ms + " ms"
                color: entry.modelData.ok ? Commons.Color.muted : Commons.Color.urgent
                elide: Text.ElideRight
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
              }
            }
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: q.load(entry.modelData.sql, q.drawerPage === "saved" ? entry.modelData : null)
            }
            Ui.Button {
              id: delBtn
              visible: q.drawerPage === "saved"
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "✕"
              tooltipText: "Delete this saved query"
              onClicked: helper.request("query.delete_saved", { id: entry.modelData.id }, function(r, e) {
                if (e) q.notify(e.message)
                if (q.savedId === entry.modelData.id) { q.savedId = ""; q.savedName = "" }
                q.loadSaved()
              })
            }
          }
          Text {
            textFormat: Text.PlainText
            visible: drawerList.count === 0
            width: parent.width
            wrapMode: Text.Wrap
            text: q.drawerPage === "history" ? "Queries you run on this connection show up here." : "Save a query with Ctrl+S to keep it here."
            color: Commons.Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
        }
      }
    }
  }

  Popup {
    id: fileMenu
    padding: 4
    width: 200
    background: Rectangle { color: Commons.Color.menu.background; border.color: Commons.Color.menu.border; border.width: 1 }
    Column {
      width: parent.width
      Repeater {
        model: [{ label: "Open SQL file…", act: "open" }, { label: "Save to file…", act: "save" },
                { label: "Run SQL file…", act: "run" }]
        delegate: Ui.Button {
          required property var modelData
          width: fileMenu.width - 8
          leftAlign: true
          text: modelData.label
          enabled: modelData.act !== "run" || !q.running
          onClicked: {
            fileMenu.close()
            if (modelData.act === "open") q.openFile()
            else if (modelData.act === "save") q.saveFile(false, "")
            else q.runFile()
          }
        }
      }
    }
  }
  FilePicker { id: picker; helper: q.helper }
  PlanView { id: planView; helper: q.helper; sid: q.sid; engine: q.engine; onNotify: function(m) { q.notify(m) } }
  Confirm { id: confirm }

  Popup {
    id: savePopup
    anchors.centerIn: parent
    width: Math.min(parent.width - 80, 420)
    padding: 16
    modal: true
    focus: true
    background: Rectangle { color: Commons.Color.popups.background; border.color: Commons.Color.popups.border; border.width: 1 }
    onOpened: { nameField.text = q.savedName; nameField.forceActiveFocus(); nameField.selectAll() }
    onClosed: editor.focusEditor()
    ColumnLayout {
      anchors.fill: parent
      spacing: 10
      Text { textFormat: Text.PlainText; text: q.savedId ? "Save query" : "Save query as"; color: Commons.Color.foreground; font.family: Style.font.family; font.pixelSize: Style.font.body; font.bold: true }
      Ui.TextField {
        id: nameField
        readOnly: !activeFocus   // an unfocused field reads no clipboard
        Layout.fillWidth: true
        placeholderText: "Name"
        font.family: Style.font.family
        onAccepted: if (text.trim()) q.save(text.trim())
      }
      RowLayout {
        Item { Layout.fillWidth: true }
        Ui.Button { text: "Cancel"; onClicked: savePopup.close() }
        Ui.Button { bordered: true; foreground: Commons.Color.accent; text: "Save"; enabled: nameField.text.trim() !== ""; onClicked: q.save(nameField.text.trim()) }
      }
    }
  }
}
