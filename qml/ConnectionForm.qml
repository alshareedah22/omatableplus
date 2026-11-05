import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "DbUrl.js" as DbUrl

// Create or edit one connection. Passwords are only sent when typed, so an
// untouched field keeps the saved one.
Flickable {
  id: form
  property var helper: null
  property var conn: null          // public connection dict, or null for a new one
  property bool keyring: true
  property string errorText: ""
  property bool busy: false

  signal saved(var conn)
  signal cancelled()
  signal deleted(string id)
  signal duplicated(var conn)
  signal connectRequested(var conn)

  readonly property var engines: [
    { id: "postgresql", title: "PostgreSQL", port: 5432 }, { id: "mysql", title: "MySQL", port: 3306 },
    { id: "mariadb", title: "MariaDB", port: 3306 }, { id: "sqlite", title: "SQLite", port: 0 },
    { id: "redis", title: "Redis", port: 6379 }, { id: "valkey", title: "Valkey", port: 6379 },
    { id: "mongodb", title: "MongoDB", port: 27017 }]
  readonly property var tags: ["local", "development", "staging", "production"]
  readonly property var sshAuths: ["agent", "key", "password"]
  readonly property var tlsModes: ["prefer", "require", "disable"]
  readonly property string engine: engineBox.value
  readonly property bool isNew: !conn || !conn.id
  readonly property bool fromOmadb: !!conn && conn.source === "omadb"
  property string testText: ""
  property bool testOk: false
  property string authSource: ""   // MongoDB only, from an imported URL
  property string urlText: ""      // result of the last URL import
  property bool urlOpen: false     // the URL field is shown

  contentHeight: body.implicitHeight + 32
  clip: true

  function load(c) {
    conn = c
    errorText = ""
    const v = c || {}
    nameField.setText(v.name || "")
    testText = ""
    urlText = ""
    urlOpen = false
    urlField.setText("")
    authSource = v.auth_source || ""
    tlsBox.value = v.tls || "prefer"
    engineBox.value = v.engine || "postgresql"
    hostField.setText(v.host || "127.0.0.1")
    portField.setText(v.port ? String(v.port) : String(engineInfo(engineBox.value).port))
    userField.setText(v.user || "")
    passField.setText("")
    passField.placeholder = v.has_password ? "Saved (leave empty to keep)" : ""
    dbField.setText(v.database || "")
    pathField.setText(v.path || "")
    tagBox.value = v.tag || "local"
    colorField.setText(v.color || "")
    folderField.setText(v.folder || "")
    readonlySwitch.checked = !!v.readonly
    const ssh = v.ssh || {}
    sshSwitch.checked = !!ssh.enabled
    sshHost.setText(ssh.host || "")
    sshPort.setText(String(ssh.port || 22))
    sshUser.setText(ssh.user || "")
    sshAuthBox.value = ssh.auth || "agent"
    sshKey.setText(ssh.key_path || "")
    sshPass.setText("")
    sshPass.placeholder = v.has_ssh_password ? "Saved (leave empty to keep)" : ""
    nameField.focusInput()
  }

  // Fill the form from a pasted database URL. The URL replaces the connection
  // fields; name, tag, folder and SSH stay as they are.
  function importUrl() {
    const u = DbUrl.parse(urlField.text)
    if (u.error) { urlText = u.error; return }
    urlText = ""
    errorText = ""
    testText = ""
    engineBox.value = u.engine
    if (u.engine === "sqlite") {
      pathField.setText(u.path)
    } else {
      hostField.setText(u.host || "127.0.0.1")
      portField.setText(String(u.port || engineInfo(u.engine).port))
      userField.setText(u.user || "")
      passField.setText(u.password || "")
      passField.dirty = u.password !== undefined
      dbField.setText(u.database || "")
      tlsBox.value = u.tls || "prefer"
      authSource = u.auth_source || ""
    }
    if (!nameField.text) {
      const where = u.engine === "sqlite" ? u.path.replace(/^.*\//, "") : (u.host || "localhost")
      nameField.setText(u.database && u.engine !== "redis" && u.engine !== "valkey" ? u.database + " @ " + where : where)
    }
    urlField.setText("")
    urlOpen = false
    urlText = "Filled in from the URL: check the fields, then Save"
    nameField.focusInput()
  }

  function toggleUrl() {
    urlOpen = !urlOpen
    urlText = ""
    if (urlOpen) urlField.focusInput(); else nameField.focusInput()
  }

  function engineInfo(id) {
    return engines.find(function(e) { return e.id === id }) || engines[0]
  }

  function collect() {
    const c = {
      name: nameField.text, engine: engine, host: hostField.text, port: parseInt(portField.text) || 0,
      user: userField.text, database: dbField.text, path: pathField.text,
      tag: tagBox.value, color: colorField.text || null, folder: folderField.text, readonly: readonlySwitch.checked,
      tls: tlsBox.value, auth_source: engine === "mongodb" ? authSource : "",
      ssh: sshSwitch.checked ? { enabled: true, host: sshHost.text, port: parseInt(sshPort.text) || 22,
        user: sshUser.text, auth: sshAuthBox.value, key_path: sshKey.text } : null
    }
    if (!isNew && !fromOmadb) c.id = conn.id
    // OmaDBngin servers saved as a connection carry their password along.
    if (passField.dirty) c.password = passField.text
    else if (fromOmadb) c.omadb = conn.name
    if (sshPass.dirty) c.ssh_password = sshPass.text
    return c
  }

  function test() {
    if (busy) return
    busy = true
    testText = "Testing…"
    testOk = false
    const c = collect()
    if (!isNew) c.id = conn.id
    delete c.omadb
    helper.request("conn.test", { conn: c }, function(result, error) {
      busy = false
      testOk = !error
      testText = error ? error.message : "Connected to " + result.version + " in " + result.ms + " ms"
    })
  }

  function save() {
    if (busy) return
    busy = true
    errorText = ""
    const c = collect()
    const method = c.omadb ? "conn.import_omadb" : "conn.save"
    helper.request(method, c.omadb ? { name: c.omadb, conn: c } : { conn: c }, function(result, error) {
      busy = false
      if (error) { errorText = error.message; return }
      form.saved(result)
    })
  }

  ColumnLayout {
    id: body
    x: 16; y: 16
    width: form.width - 32
    spacing: 12

    RowLayout {
      Layout.fillWidth: true
      spacing: 8
      Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText
        text: form.fromOmadb ? "OmaDBngin server · " + form.conn.name : form.isNew ? "New connection" : "Edit connection"
        color: Commons.Color.foreground
        elide: Text.ElideRight
        font.family: Style.font.family
        font.pixelSize: Style.font.body * 1.4
        font.bold: true
      }
      Ui.Button {
        visible: form.isNew && !form.fromOmadb
        text: form.urlOpen ? "Hide URL import" : "Import from URL…"
        tooltipText: "Fill the form from a postgres://, mysql://, redis://, mongodb:// or sqlite:// URL"
        onClicked: form.toggleUrl()
      }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: 8
      visible: form.urlOpen && form.isNew && !form.fromOmadb
      Field {
        id: urlField
        label: "Database URL (Enter to fill in the form, Esc to close)"
        Layout.fillWidth: true
        placeholder: "postgres://user:password@host:5432/database"
        onAccepted: form.importUrl()
        Keys.onEscapePressed: form.toggleUrl()
      }
      Ui.Button {
        Layout.alignment: Qt.AlignBottom
        Layout.preferredHeight: urlField.inputHeight
        bordered: true
        text: "Import"
        enabled: urlField.text.trim() !== ""
        onClicked: form.importUrl()
      }
    }
    Text {
      textFormat: Text.PlainText
      Layout.fillWidth: true
      visible: form.urlText !== "" && form.isNew && !form.fromOmadb
      text: form.urlText
      color: form.urlText.indexOf("Filled in") === 0 ? Commons.Color.accent : Commons.Color.urgent
      wrapMode: Text.Wrap
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    Field { id: nameField; label: "Name"; Layout.fillWidth: true; placeholder: "My database" }

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      Column {
        spacing: 4
        Layout.fillWidth: true
        Text { textFormat: Text.PlainText; text: "Engine"; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
        Ui.Dropdown {
          id: engineBox
          width: parent.width
          showLabel: false
          enabled: !form.fromOmadb
          opacity: enabled ? 1 : 0.5
          value: "postgresql"
          options: form.engines.map(function(e) { return { value: e.id, label: e.title } })
          onChanged: function(v) { portField.setText(String(form.engineInfo(v).port)) }
        }
      }
      Column {
        spacing: 4
        Layout.preferredWidth: 160
        Text { textFormat: Text.PlainText; text: "Tag"; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
        Ui.Dropdown { id: tagBox; width: parent.width; showLabel: false; value: "local"; options: form.tags }
      }
      Field { id: colorField; label: "Color"; Layout.preferredWidth: 110; placeholder: "#rrggbb" }
    }

    Field { id: pathField; label: "Database file"; Layout.fillWidth: true; visible: form.engine === "sqlite"; placeholder: "/path/to/database.db" }

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      visible: form.engine !== "sqlite"
      Field { id: hostField; label: "Host"; Layout.fillWidth: true; enabled: !form.fromOmadb }
      Field { id: portField; label: "Port"; Layout.preferredWidth: 110; enabled: !form.fromOmadb }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      visible: form.engine !== "sqlite"
      Field { id: userField; label: "User"; Layout.fillWidth: true }
      Field { id: passField; label: "Password"; Layout.fillWidth: true; secret: true }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      visible: form.engine !== "sqlite"
      Field {
        id: dbField
        Layout.fillWidth: true
        label: form.engine === "redis" || form.engine === "valkey" ? "Database index" : "Database"
      }
      Column {
        spacing: 4
        Layout.preferredWidth: 110
        Text { textFormat: Text.PlainText; text: "TLS"; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
        Ui.Dropdown { id: tlsBox; width: parent.width; showLabel: false; value: "prefer"; options: form.tlsModes }
      }
    }

    Text {
      textFormat: Text.PlainText
      Layout.fillWidth: true
      visible: !form.keyring && form.engine !== "sqlite"
      text: "No keyring is available: passwords are stored in ~/.config/omatableplus/connections.json (readable only by you)."
      color: Commons.Color.muted
      wrapMode: Text.Wrap
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      Field { id: folderField; label: "Folder (groups connections in the list)"; Layout.fillWidth: true; placeholder: "e.g. Work, Clients/Acme" }
    }
    Ui.Toggle {
      id: readonlySwitch
      Layout.fillWidth: true
      label: "Read-only"
      description: "Browse and query only: OmaTablePlus refuses every change, and the server session is read-only too"
      onClicked: checked = !checked
    }

    // ---- SSH tunnel ----
    Ui.Toggle {
      id: sshSwitch
      Layout.fillWidth: true
      visible: form.engine !== "sqlite"
      label: "Connect over SSH"
      description: "Tunnel through a jump host with ssh -L"
      onClicked: checked = !checked
    }

    GridLayout {
      Layout.fillWidth: true
      visible: sshSwitch.checked && form.engine !== "sqlite"
      columns: 2
      columnSpacing: 12
      rowSpacing: 12
      Field { id: sshHost; label: "SSH host"; Layout.fillWidth: true }
      Field { id: sshPort; label: "SSH port"; Layout.preferredWidth: 110 }
      Field { id: sshUser; label: "SSH user"; Layout.fillWidth: true }
      Column {
        spacing: 4
        Layout.preferredWidth: 110
        Text { textFormat: Text.PlainText; text: "Auth"; color: Commons.Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
        Ui.Dropdown { id: sshAuthBox; width: parent.width; showLabel: false; value: "agent"; options: form.sshAuths }
      }
      Field { id: sshKey; label: "Key file"; Layout.fillWidth: true; Layout.columnSpan: 2; visible: sshAuthBox.value === "key"; placeholder: "~/.ssh/id_ed25519" }
      Field { id: sshPass; label: "SSH password"; Layout.fillWidth: true; Layout.columnSpan: 2; visible: sshAuthBox.value === "password"; secret: true }
    }

    Text {
      textFormat: Text.PlainText
      Layout.fillWidth: true
      visible: form.errorText !== ""
      text: form.errorText
      color: Commons.Color.urgent
      wrapMode: Text.Wrap
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }

    Text {
      textFormat: Text.PlainText
      Layout.fillWidth: true
      visible: form.testText !== ""
      text: form.testText
      color: form.testOk ? Commons.Color.accent : form.busy ? Commons.Color.muted : Commons.Color.urgent
      wrapMode: Text.Wrap
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: 8
      Ui.Button { bordered: true; text: form.fromOmadb ? "Save as connection" : "Save"; enabled: !form.busy; onClicked: form.save() }
      Ui.Button { bordered: true; text: "Test"; enabled: !form.busy && form.engine !== "redis" && form.engine !== "valkey" && form.engine !== "mongodb"; onClicked: form.test() }
      Ui.Button {
        bordered: true
        text: "Connect"
        visible: !form.isNew
        enabled: !form.busy
        // an OmaDBngin server connects as it is, plus the Read-only switch
        onClicked: form.connectRequested(form.fromOmadb ? Object.assign({}, form.conn, { readonly: readonlySwitch.checked }) : form.conn)
      }
      Ui.Button { text: "Cancel"; onClicked: form.cancelled() }
      Item { Layout.fillWidth: true }
      Ui.Button {
        bordered: true
        text: "Duplicate"
        visible: !form.isNew && !form.fromOmadb
        onClicked: form.helper.request("conn.duplicate", { id: form.conn.id }, function(r, e) {
          if (e) form.errorText = e.message; else form.duplicated(r)
        })
      }
      Ui.Button {
        id: deleteButton
        bordered: true
        foreground: armed ? Commons.Color.urgent : Commons.Color.foreground
        property bool armed: false
        text: armed ? "Click again to delete" : "Delete"
        visible: !form.isNew && !form.fromOmadb
        onClicked: {
          if (!armed) { armed = true; disarm.restart(); return }
          armed = false
          form.helper.request("conn.delete", { id: form.conn.id }, function(r, e) {
            if (e) form.errorText = e.message; else form.deleted(form.conn.id)
          })
        }
        Timer { id: disarm; interval: 3000; onTriggered: deleteButton.armed = false }
      }
    }
  }
}
