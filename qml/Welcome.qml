import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

// TablePlus-style start screen: connections on the left, the form on the right.
Item {
  id: welcome
  property var helper: null
  property var connections: []
  property var omadbServers: []
  property bool keyring: true
  property string search: ""
  property string selectedId: ""
  property string toast: ""
  property bool formOpen: false

  signal connectRequested(var conn)

  readonly property var tagOrder: ["production", "staging", "development", "local"]
  readonly property var tagColors: ({ production: "#e05252", staging: "#e0a352", development: "#52a3e0", local: "#7a8a7a" })
  readonly property var engineIcons: ({ postgresql: "", mysql: "", mariadb: "", sqlite: "",
    redis: "", valkey: "", mongodb: "" })

  function reload() {
    helper.request("conn.list", {}, function(result, error) {
      if (error) { toast = error.message; return }
      connections = result.connections
      omadbServers = result.omadb
      keyring = result.keyring
    })
  }

  function matches(c) {
    const q = search.toLowerCase()
    return q === "" || (c.name + " " + c.host + " " + c.engine + " " + c.database).toLowerCase().indexOf(q) !== -1
  }

  // Rows for the list: a header per group, then its connections.
  readonly property var rows: {
    const out = []
    const omadb = omadbServers.filter(matches)
    if (omadb.length) {
      out.push({ header: "OmaDBngin" })
      omadb.forEach(function(c) { out.push({ conn: c }) })
    }
    // folders first (A–Z, collapsible), then the rest by tag
    const folders = {}
    connections.forEach(function(c) { if (c.folder && matches(c)) (folders[c.folder] = folders[c.folder] || []).push(c) })
    Object.keys(folders).sort(function(a, b) { return a.localeCompare(b) }).forEach(function(f) {
      const shut = !!collapsed[f] && search === ""
      out.push({ header: (shut ? "▸ " : "▾ ") + f + "  " + folders[f].length, folder: f })
      if (!shut) folders[f].forEach(function(c) { out.push({ conn: c }) })
    })
    tagOrder.forEach(function(tag) {
      const group = connections.filter(function(c) { return !c.folder && c.tag === tag && matches(c) })
      if (!group.length) return
      out.push({ header: tag.charAt(0).toUpperCase() + tag.slice(1) })
      group.forEach(function(c) { out.push({ conn: c }) })
    })
    return out
  }

  property var collapsed: ({})     // folder -> true when shut
  function toggleFolder(f) { const c = Object.assign({}, collapsed); c[f] = !c[f]; collapsed = c }

  function select(c) {
    selectedId = c ? c.id : ""
    formOpen = true
    form.item.load(c)
  }

  function subtitle(c) {
    if (c.engine === "sqlite") return c.path
    return (c.user ? c.user + "@" : "") + c.host + ":" + c.port + (c.database ? "/" + c.database : "")
  }

  Component.onCompleted: if (helper && helper.status === "ready") reload()  // else onStatusChanged loads it

  Connections {
    target: welcome.helper
    function onStatusChanged() { if (welcome.helper.status === "ready") welcome.reload() }
  }

  RowLayout {
    anchors.fill: parent
    spacing: 0

    // ---- connection list ----
    Rectangle {
      Layout.preferredWidth: 340
      Layout.fillHeight: true
      color: Qt.darker(Commons.Color.background, 1.15)

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        RowLayout {
          Layout.fillWidth: true
          Ui.TextField {
            id: searchField
            readOnly: !activeFocus   // an unfocused field reads no clipboard
            Layout.fillWidth: true
            placeholderText: "Search connections"
            font.family: Style.font.family
            onTextChanged: welcome.search = text
            Keys.onDownPressed: list.forceActiveFocus()
          }
          Ui.Button { bordered: true; text: "+"; tooltipText: "New connection"; onClicked: welcome.select(null) }
        }

        ListView {
          id: list
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: welcome.rows
          keyNavigationEnabled: true
          Keys.onReturnPressed: {
            const r = welcome.rows[currentIndex]
            if (r && r.conn) welcome.connectRequested(r.conn)
          }

          Text {
            textFormat: Text.PlainText
            y: 8
            width: list.width
            visible: welcome.rows.length === 0
            text: welcome.search ? "No matches" : "No connections yet. Press + to add one."
            color: Commons.Color.muted
            wrapMode: Text.Wrap
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }

          delegate: Item {
            required property var modelData
            required property int index
            width: list.width
            height: modelData.header ? 28 : 52

            Text {
              textFormat: Text.PlainText
              visible: !!modelData.header
              anchors.bottom: parent.bottom
              anchors.bottomMargin: 4
              text: modelData.header || ""
              color: modelData.folder ? Commons.Color.foreground : Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              font.bold: true
            }
            MouseArea {
              visible: !!modelData.folder
              anchors.fill: parent
              onClicked: welcome.toggleFolder(modelData.folder)
            }

            Rectangle {
              visible: !modelData.header
              anchors.fill: parent
              anchors.topMargin: 2
              anchors.bottomMargin: 2
              radius: 6
              color: modelData.conn && modelData.conn.id === welcome.selectedId
                ? Qt.lighter(Commons.Color.background, 1.5) : hover.containsMouse ? Qt.lighter(Commons.Color.background, 1.25) : "transparent"

              Rectangle { width: 4; height: parent.height - 12; anchors.verticalCenter: parent.verticalCenter; x: 4; radius: 2
                color: modelData.conn ? modelData.conn.color : "transparent" }

              Text {
                id: icon
                textFormat: Text.PlainText
                x: 16
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.conn ? (welcome.engineIcons[modelData.conn.engine] || "") : ""
                color: Commons.Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body * 1.6
              }
              Column {
                anchors.left: icon.right
                anchors.leftMargin: 12
                anchors.right: badge.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  elide: Text.ElideRight
                  text: modelData.conn ? modelData.conn.name : ""
                  color: Commons.Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }
                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  elide: Text.ElideRight
                  text: modelData.conn ? welcome.subtitle(modelData.conn) : ""
                  color: Commons.Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                }
              }
              Text {
                id: badge
                textFormat: Text.PlainText
                anchors.right: parent.right
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                text: !modelData.conn ? "" : modelData.conn.source === "omadb" ? (modelData.conn.running ? "●" : "○")
                  : (modelData.conn.readonly ? "\uf023 " : "") + (modelData.conn.ssh ? "" : "")
                color: modelData.conn && modelData.conn.source === "omadb" && modelData.conn.running ? "#5fb85f" : Commons.Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }

              MouseArea {
                id: hover
                anchors.fill: parent
                hoverEnabled: true
                onClicked: welcome.select(modelData.conn)
                onDoubleClicked: welcome.connectRequested(modelData.conn)
              }
            }
          }
        }

      }
    }

    // ---- form / empty state ----
    Item {
      Layout.fillWidth: true
      Layout.fillHeight: true

      Column {
        anchors.centerIn: parent
        visible: !welcome.formOpen
        spacing: 8
        Image {
          anchors.horizontalCenter: parent.horizontalCenter
          source: Qt.resolvedUrl("../icon.svg")
          width: 88
          height: 88
          sourceSize: Qt.size(176, 176)
          opacity: 0.85
        }
        Text { textFormat: Text.PlainText; anchors.horizontalCenter: parent.horizontalCenter; text: "Select a connection, or press + to create one"
          color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.body }
      }

      // Destroyed while closed: a hidden form would keep its text inputs alive.
      Loader {
        id: form
        anchors.fill: parent
        active: welcome.formOpen
        sourceComponent: ConnectionForm {
          helper: welcome.helper
          keyring: welcome.keyring
          onSaved: function(c) { welcome.reload(); welcome.select(c); welcome.toast = "Saved " + c.name }
          onCancelled: { welcome.formOpen = false; welcome.selectedId = "" }
          onDeleted: function(id) { welcome.formOpen = false; welcome.selectedId = ""; welcome.reload() }
          onDuplicated: function(c) { welcome.reload(); welcome.select(c) }
          onConnectRequested: function(c) { welcome.connectRequested(c) }
        }
      }
    }
  }

  // ---- toast ----
  Rectangle {
    anchors.bottom: parent.bottom
    anchors.horizontalCenter: parent.horizontalCenter
    anchors.bottomMargin: 16
    visible: welcome.toast !== ""
    width: toastText.implicitWidth + 24
    height: toastText.implicitHeight + 12
    radius: 6
    color: Qt.lighter(Commons.Color.background, 1.6)
    Text { textFormat: Text.PlainText; id: toastText; anchors.centerIn: parent; text: welcome.toast; color: Commons.Color.foreground
      font.family: Style.font.family; font.pixelSize: Style.font.body }
    Timer { running: welcome.toast !== ""; interval: 3000; onTriggered: welcome.toast = "" }
  }
}
