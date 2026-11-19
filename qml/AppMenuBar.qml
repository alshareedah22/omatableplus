import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Commons as Commons

// The window's menu bar: File, Edit, View, Database, Help. Items call what the window and the
// open browser already do; the shortcuts shown are the ones those places already handle (a
// second Shortcut for the same keys would make Qt ignore both).
MenuBar {
  id: bar

  property var win: null                 // the AppWindow
  readonly property var browser: win ? win.currentBrowser : null
  readonly property bool sql: !!browser && typeof browser.openQuery === "function"
  readonly property bool connected: !!win && win.active >= 0

  // ---- look: Omarchy's menu colors ----
  delegate: MenuBarItem {
    id: top
    padding: Style.space(6)
    leftPadding: Style.space(10)
    rightPadding: Style.space(10)
    contentItem: Text {
      textFormat: Text.PlainText
      text: top.text.replace("&", "")
      color: Commons.Color.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }
    background: Rectangle {
      color: top.highlighted || top.menu.visible ? Qt.rgba(Commons.Color.foreground.r, Commons.Color.foreground.g, Commons.Color.foreground.b, 0.10) : "transparent"
    }
  }
  background: Rectangle {
    implicitHeight: Style.font.bodySmall * 2.1
    color: Qt.darker(Commons.Color.background, 1.18)
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Commons.Color.menu.border }
  }

  component Item_: MenuItem {
    id: mi
    property string hint: ""
    property var run: null
    onTriggered: if (run) run()
    implicitWidth: Style.space(320)
    padding: Style.space(6)
    leftPadding: Style.space(12)
    rightPadding: Style.space(12)
    contentItem: Item {
      implicitHeight: label.implicitHeight
      Text {
        textFormat: Text.PlainText
        id: label
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: mi.text
        color: mi.enabled ? Commons.Color.foreground : Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
      Text {
        textFormat: Text.PlainText
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: mi.hint
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
    background: Rectangle {
      color: mi.highlighted && mi.enabled ? Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.18) : "transparent"
    }
  }

  component Menu_: Menu {
    background: Rectangle {
      implicitWidth: Style.space(320)
      color: Commons.Color.menu.background
      border.color: Commons.Color.menu.border
      border.width: 1
    }
  }

  component Sep_: MenuSeparator {
    contentItem: Rectangle { implicitHeight: 1; color: Commons.Color.menu.border }
  }

  // Edit actions work on whatever text field or editor has the keyboard focus.
  function onFocused(name) {
    const item = bar.win ? bar.win.activeFocusItem : null
    if (item && typeof item[name] === "function") item[name]()
  }

  Menu_ {
    title: "File"
    Item_ { text: "New window"; hint: "Ctrl+Alt+N"; run: function() { bar.win.newWindowRequested("") } }
    Item_ {
      text: "Open this connection in a new window"
      enabled: bar.connected
      run: function() { bar.win.newWindowRequested(bar.win.session ? bar.win.session.id : "") }
    }
    Sep_ {}
    Item_ { text: "Connections"; hint: "Ctrl+Shift+N"; run: function() { bar.win.active = -1 } }
    Item_ { text: "Close connection"; enabled: bar.connected; run: function() { bar.win.closeConnectionAt(bar.win.active) } }
    Sep_ {}
    Item_ { text: "Close window"; hint: "Ctrl+Shift+W"; run: function() { bar.win.visible = false } }
    Item_ { text: "Quit OmaTablePlus"; hint: "Ctrl+Q"; run: function() { bar.win.quitRequested() } }
  }

  Menu_ {
    title: "Edit"
    Item_ { text: "Undo"; hint: "Ctrl+Z"; run: function() { bar.onFocused("undo") } }
    Item_ { text: "Redo"; hint: "Ctrl+Shift+Z"; run: function() { bar.onFocused("redo") } }
    Sep_ {}
    Item_ { text: "Cut"; hint: "Ctrl+X"; run: function() { bar.onFocused("cut") } }
    Item_ { text: "Copy"; hint: "Ctrl+C"; run: function() { bar.onFocused("copy") } }
    Item_ { text: "Paste"; hint: "Ctrl+V"; run: function() { bar.onFocused("paste") } }
    Item_ { text: "Select all"; hint: "Ctrl+A"; run: function() { bar.onFocused("selectAll") } }
  }

  Menu_ {
    title: "View"
    Item_ { text: "Next connection"; hint: "Ctrl+PgDown"; enabled: bar.win && bar.win.connectionCount > 1; run: function() { bar.win.cycleConnection(1) } }
    Item_ { text: "Previous connection"; hint: "Ctrl+PgUp"; enabled: bar.win && bar.win.connectionCount > 1; run: function() { bar.win.cycleConnection(-1) } }
  }

  Menu_ {
    title: "Database"
    Item_ { text: "New query"; hint: "Ctrl+T"; enabled: bar.sql; run: function() { bar.browser.openQuery() } }
    Item_ { text: "Close tab"; hint: "Ctrl+W"; enabled: bar.sql && bar.browser.current >= 0; run: function() { bar.browser.closeTab(bar.browser.current) } }
    Sep_ {}
    Item_ { text: "Refresh tables"; enabled: bar.sql; run: function() { bar.browser.loadTree() } }
    Item_ { text: "Table diagram"; enabled: bar.sql; run: function() { bar.browser.openDiagram() } }
    Item_ { text: "Back up database…"; enabled: bar.sql; run: function() { bar.browser.backup(false) } }
    Item_ { text: "Restore…"; enabled: bar.sql; run: function() { bar.browser.restore() } }
  }

  Menu_ {
    title: "Help"
    Item_ { text: "Keyboard shortcuts"; run: function() { bar.win.showShortcuts() } }
    Item_ { text: "README on GitHub"; run: function() { Qt.openUrlExternally("https://github.com/blackcodesh/omatableplus#readme") } }
    Item_ { text: "About OmaTablePlus"; run: function() { bar.win.showAbout() } }
  }
}
